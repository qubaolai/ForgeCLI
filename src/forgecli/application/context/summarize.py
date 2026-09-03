"""二级压缩: 用模型把一批消息换成一段交接说明 (ADR-0032 决策 2).

只在一级降级压不下去之后才走. 它引入两样一级没有的东西: 一次额外的模型调用 (要钱,
要时间), 和**不可复现性** —— 被摘要吃掉的原文再也拼不回来, 所以摘要正文必须进事件
payload 而不是留引用 (决策 8).

"要钱"这件事以前只写在这段注释里: `response.usage` 被原地丢掉, 于是这次调用不进
`USAGE_RECORDED`, 也不进本轮合计. 而它的 input 大致等于被压掉的那段历史, 不是零头.
现在交回一份 `UsageRecordDraft` (ADR-0037), 由调用方按与模型调用完全相同的那条路落盘.

## 两条实现上的硬约束

1. **切点必须落在 tool call 配对之外.** 带 tool_calls 的 assistant 消息后面欠着配对的
   tool result, 从中间切开供应商会拒掉整个请求. 而这次失败发生在上下文已经超长的时候,
   再失败一次这一轮就彻底没救了. 切点由 ``safe_split_points`` 给出.
2. **送去摘要的是拍平的文本, 不是原消息.** 把带 tool_calls 的消息原样转发给模型, 等于
   让它去解读一段工具调用协议, 而我们要的只是"这段对话讲了什么". 拍平之后既没有配对
   问题, 也不会让模型误以为那些工具调用是它现在该接着做的事.
"""

from __future__ import annotations

import json
import uuid

from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.application.llm.metering import UsageMeter
from forgecli.application.prompt.template_renderer import render_notice
from forgecli.domain.context.window import EvictionPlan
from forgecli.domain.conversation.message import (
    ChatMessage,
    TextBlock,
    ToolResultBlock,
)
from forgecli.domain.conversation.turn import MessageRole
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.params import ModelParams
from forgecli.domain.model.request import ModelRequest
from forgecli.domain.model.usage import UsageRecordDraft
from forgecli.domain.tool.tool_call import ToolCall

__all__ = ["Summary", "summarize"]

# 拍平工具调用参数时每个值留多少字符. 够认出路径与关键选项, 不够把补丁正文带进来.
_ARGUMENT_PREVIEW_CHARS = 120

# 送进摘要请求的那段历史最多多少字符 (约 30k token 的三分之一). 见 `_flatten`.
_MAX_FLATTENED_CHARS = 30_000


class Summary:
    """摘要结果. 拿不到摘要时 ``text`` 为空, 调用方按"压不下去"处理.

    ``usage`` 与 ``text`` 相互独立: 模型回了一段空白也照样计费, 所以只要请求发出去过,
    这里就带着计量草稿 —— 按"没压动"处理的那条路径同样要把这笔账记上.
    """

    __slots__ = ("messages_replaced", "model", "provider", "text", "usage")

    def __init__(
        self,
        *,
        text: str = "",
        messages_replaced: int = 0,
        provider: str = "",
        model: str = "",
        usage: UsageRecordDraft | None = None,
    ) -> None:
        self.text = text
        self.messages_replaced = messages_replaced
        self.provider = provider
        self.model = model
        self.usage = usage


def summarize(
    plan: EvictionPlan,
    gateway: LlmGateway,
    *,
    session_id: str,
    turn_id: str,
    meter: UsageMeter | None = None,
) -> tuple[tuple[ChatMessage, ...], Summary]:
    """把被淘汰的那一段换成一份交接说明, 返回淘汰后的完整窗口.

    ## 为什么摘要没有随 ADR-0041 一起删掉

    工具结果的结论已经在 ``summary`` 与 ``data`` 里了, 但被淘汰区间里还有 assistant 的
    **自然语言** —— 取舍理由, 承诺, 对用户约束的复述. 那些提取不出来, 丢了就是丢了.
    用户原话不走这条路: 它便宜且精确, 由 ``EvictionPlan`` 逐字保留 (决策 5).

    ## 与 ADR-0032 的那次摘要有什么不同

    触发点从"压不下去了"改成"撞高水位". 高水位对模型窗口还有大把余量, 摘要不再是在
    上下文已经吃紧时跑 —— 那时候摘不好就没有第二次机会了.

    ``meter`` 缺省为 None: 没接计量时照常摘要, 只是这次调用不产出计量草稿.
    """
    request = ModelRequest(
        request_id=f"req_{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        turn_id=turn_id,
        # 用途标签, 不选模型 (ADR-0011 §3.3): 摘要走当前主模型, 除非用户显式
        # 给这个用途配了覆盖.
        origin=RequestOrigin.COMPACT,
        messages=(
            ChatMessage(
                role=MessageRole.USER,
                content=(
                    TextBlock(
                        f"{render_notice("context.compaction_instruction")}\n\n"
                        f"{_flatten(plan.dropped)}"
                    ),
                ),
            ),
        ),
        params=ModelParams(),
        # 不带系统提示词也不带工具: 这一次调用不是在扮演 Agent, 它只做一件事.
        # 带上工具目录, 模型会开始"请求工具"而不是写摘要.
        system_prompt=None,
        tools=(),
    )
    response = gateway.complete(request)
    # 草稿在这里就建好: 下面那条"没摘出东西"的返回路径同样要带着它. 请求已经发出去了,
    # 这笔钱花没花与摘要好不好用无关.
    usage = None if meter is None else meter.build_draft(request, response)
    text = response.content.strip()
    if not text:
        # 摘不出东西也照样淘汰: 窗口撞了高水位, 不淘汰这一轮就发不出去.
        return (_verbatim_block(plan), *plan.kept), Summary(
            messages_replaced=len(plan.dropped), usage=usage
        )
    return (_summary_block(plan, text), *plan.kept), Summary(
        text=text,
        messages_replaced=len(plan.dropped),
        provider=response.provider,
        model=response.model,
        usage=usage,
    )


def _summary_block(plan: EvictionPlan, text: str) -> ChatMessage:
    """淘汰后窗口的第一条: 交接说明 + 逐字保留的用户原话.

    合成一条而不是两条, 是因为它们描述的是同一段被丢掉的历史; 拆成两条之后, 下一次淘汰
    的切点计算还要额外考虑"别把这一对切开".
    """
    return ChatMessage(
        role=MessageRole.USER,
        content=(
            TextBlock(
                f"{render_notice("context.compaction_header")}\n\n{text}"
                f"{_verbatim_section(plan)}"
            ),
        ),
    )


def _verbatim_block(plan: EvictionPlan) -> ChatMessage:
    """摘要为空时的兜底: 至少把用户原话留住.

    模型的话丢了是损失, 用户的话丢了是错误 —— 那是这次任务的目标与约束本身.
    """
    return ChatMessage(
        role=MessageRole.USER,
        content=(
            TextBlock(
                f"{render_notice("context.compaction_header")}\n\n"
                f"{_verbatim_section(plan).strip() or _nothing_kept()}"
            ),
        ),
    )


def _nothing_kept() -> str:
    return render_notice("context.compaction_nothing_kept")


def _verbatim_section(plan: EvictionPlan) -> str:
    """被淘汰区间里的用户原话, 逐字.

    不走摘要: 用户约束是摘要最容易丢, 丢了也最贵的东西, 而它便宜 —— 一次会话十几条,
    占窗口不到 2%. 花那点 token 换"目标不会被转述走样", 是这一整套里最划算的一笔.
    """
    if not plan.preserved_user_messages:
        return ""
    lines = [
        _block_text(block)
        for message in plan.preserved_user_messages
        for block in message.content
    ]
    body = "\n".join(line for line in lines if line.strip())
    if not body:
        return ""
    return f"\n\n{render_notice("context.compaction_user_verbatim")}\n{body}"


def _flatten(messages: tuple[ChatMessage, ...]) -> str:
    """拍平成带角色前缀的文本, 并封顶.

    **封顶是必须的**: 淘汰刻意挑最靠后的合法切点 (丢得越多, 下一次淘汰离得越远), 所以
    交到这里的这一段是系统性最大的一段. 不封顶的话, 一个 30k 的窗口撞水位时, 摘要调用
    自己就要发 30k 输入 —— 而它极可能因此超窗抛异常, 从 fit() 一路穿出去, 把本该被救回
    的这一轮判死.

    截掉的是**前面**那一半: 交接说明是给"接着往下做"用的, 越靠后越相关; 而更早那部分里
    真正不能丢的目标与约束, 已经由 EvictionPlan 逐字保留了, 不靠这条路.

    角色名取枚举值而不是另写一套中文标签: 那是事实不是措辞, 抄一份到 text.py 就是
    第二份会漂的真相 (ADR-0031 的收录判据).
    """
    lines: list[str] = []
    for message in messages:
        body = "\n".join(_block_text(block) for block in message.content)
        if message.tool_calls:
            calls = "\n".join(f"-> {_call_line(call)}" for call in message.tool_calls)
            body = f"{body}\n{calls}" if body else calls
        if body.strip():
            lines.append(f"{message.role.value}: {body}")
    flat = "\n\n".join(lines)
    if len(flat) <= _MAX_FLATTENED_CHARS:
        return flat
    notice = render_notice("context.compaction_input_clipped")
    return f"{notice}\n\n{flat[-_MAX_FLATTENED_CHARS:]}"


def _call_line(call: ToolCall) -> str:
    """工具调用留名字加一段截短的参数.

    只留名字的话, 摘要模型看到的是 `-> fs_apply_patch`, 读不出这一步动的是哪个文件 ——
    而"改过或正在关注的文件"正是压缩指令点名要保住的东西.

    参数也不能逐字留: 补丁正文是 transcript 里最大的一块, 原样送进摘要请求, 等于把
    正要压缩掉的内容再完整发一遍.
    """
    arguments = ", ".join(
        f"{name}={_clipped(value)}" for name, value in call.arguments.items()
    )
    return f"{call.name}({arguments})"


def _clipped(value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if len(text) <= _ARGUMENT_PREVIEW_CHARS:
        return text
    dropped = len(text) - _ARGUMENT_PREVIEW_CHARS
    return f"{text[:_ARGUMENT_PREVIEW_CHARS]}...(+{dropped})"


def _block_text(block: object) -> str:
    if isinstance(block, TextBlock):
        return block.text
    if isinstance(block, ToolResultBlock):
        return block.content
    return ""
