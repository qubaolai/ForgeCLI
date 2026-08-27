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

from forgecli.application.context.transcript import safe_split_points
from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.application.llm.metering import UsageMeter
from forgecli.application.prompt.template_renderer import render_notice
from forgecli.domain.conversation.message import (
    ChatMessage,
    TextBlock,
    ToolResultBlock,
)
from forgecli.domain.conversation.turn import MessageRole
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.params import ModelParams
from forgecli.domain.model.request import ModelRequest
from forgecli.domain.model.selection import CurrentModelSelection
from forgecli.domain.model.usage import UsageRecordDraft
from forgecli.domain.tool.tool_call import ToolCall

__all__ = ["KEEP_RECENT_MESSAGES", "Summary", "summarize"]

# 摘要之后至少留几条原始消息.
#
# 留的是**最近**的: 模型正在做的那件事全在这几条里, 换成摘要等于让它凭一段转述接着
# 干活. 6 条大致覆盖"用户提问 + 一次工具往返 + 一次回答"这样一个完整片段.
KEEP_RECENT_MESSAGES = 6

# 拍平工具调用参数时每个值留多少字符. 够认出路径与关键选项, 不够把补丁正文带进来.
_ARGUMENT_PREVIEW_CHARS = 120


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
    messages: tuple[ChatMessage, ...],
    gateway: LlmGateway,
    *,
    session_id: str,
    turn_id: str,
    meter: UsageMeter | None = None,
) -> tuple[tuple[ChatMessage, ...], Summary]:
    """把靠前的一段换成摘要. 压不动时原样返回.

    ``meter`` 缺省为 None: 没接计量时照常摘要, 只是这次调用不产出计量草稿. 与
    ``gateway`` 缺省为 None 是同一条取舍 —— 缺一个可选协作件不该让功能塌掉.
    """
    split = _split_at(messages)
    if split <= 0:
        return messages, Summary()
    head = messages[:split]
    request = ModelRequest(
        request_id=f"req_{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        turn_id=turn_id,
        # 用途标签, 不选模型 (ADR-0011 §3.3): 摘要走当前主模型, 除非用户显式
        # 给这个用途配了覆盖.
        origin=RequestOrigin.COMPACT,
        model_selection=CurrentModelSelection(),
        messages=(
            ChatMessage(
                role=MessageRole.USER,
                content=(
                    TextBlock(
                        f"{render_notice("context.compaction_instruction")}\n\n{_flatten(head)}"
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
    # 草稿在这里就建好: 下面两条"没压动"的返回路径同样要带着它. 请求已经发出去了,
    # 这笔钱花没花与摘要好不好用无关.
    usage = None if meter is None else meter.build_draft(request, response)
    text = response.content.strip()
    if not text:
        return messages, Summary(usage=usage)
    replacement = ChatMessage(
        role=MessageRole.USER,
        content=(TextBlock(f"{render_notice("context.compaction_header")}\n\n{text}"),),
    )
    return (replacement, *messages[split:]), Summary(
        text=text,
        messages_replaced=split,
        provider=response.provider,
        model=response.model,
        usage=usage,
    )


def _split_at(messages: tuple[ChatMessage, ...]) -> int:
    """在不破坏配对的前提下, 能切掉的最靠后的位置."""
    ceiling = len(messages) - KEEP_RECENT_MESSAGES
    if ceiling <= 0:
        return 0
    usable = [point for point in safe_split_points(messages) if 0 < point <= ceiling]
    return max(usable) if usable else 0


def _flatten(messages: tuple[ChatMessage, ...]) -> str:
    """拍平成带角色前缀的文本.

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
    return "\n\n".join(lines)


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
