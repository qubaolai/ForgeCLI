"""请求前 token 估算 TokenEstimator（ADR-0011 §11.4）。

统一的调用前输入 token 估算组件，服务于两件事：
    1. 判断输入是否超出所选模型上下文窗口（gateway 请求前预检查）。
    2. 上下文管理据此决定要不要压缩（ADR-0032 决策 1）。

估算覆盖 system prompt、messages（含 tool result 与 tool call 参数）、tools schema。
估算只读、不写盘；近似误差不阻塞调用，但估算得出的 usage 必须标 estimated=True。

近似方式是字符折算，**刻意偏保守**：少算的后果是发出一个必然被供应商拒掉的请求，
一整轮工作连同已经花掉的 token 一起作废；多算的后果只是压缩早触发一点。精确分词器
（按 provider/model 选择）留后续接入，接口不变。
"""

from __future__ import annotations

import json
import re

from forgecli.domain.conversation.message import (
    ChatMessage,
    ContentBlock,
    TextBlock,
    ToolResultBlock,
)
from forgecli.domain.tool.tool_call import ToolCall, ToolSchema

# 中日韩表意文字与全角标点. 与拉丁字符分开折算: 同样的字符数, 这一段密得多.
_CJK = re.compile(r"[　-〿぀-ヿ㐀-䶿一-鿿豈-﫿＀-￯]")
# 拉丁与代码: 3 而不是 4. 实测同一批 transcript, 代码为主的部分 deepseek 是 3.05,
# GLM 是 3.89 字符每 token —— 取 3 对两家都不会少算.
_CHARS_PER_TOKEN = 3
# 每条消息的结构开销（角色标记、分隔符等）。
_PER_MESSAGE_OVERHEAD = 4


class ApproximateTokenEstimator:
    """字符比例近似估算器。确定性、无 IO，可直接用于单测。"""

    def estimate_input(
        self,
        *,
        messages: tuple[ChatMessage, ...],
        system_prompt: str | None = None,
        tools: tuple[ToolSchema, ...] = (),
    ) -> int:
        total = 0
        if system_prompt:
            total += self.estimate_text(system_prompt)
        for message in messages:
            total += _PER_MESSAGE_OVERHEAD
            for block in message.content:
                total += self.estimate_text(_block_text(block))
            for call in message.tool_calls:
                total += self.estimate_text(_tool_call_text(call))
        for tool in tools:
            schema_text = json.dumps(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": dict(tool.parameters),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            total += self.estimate_text(schema_text)
        return total

    def estimate_text(self, text: str) -> int:
        if not text:
            return 0
        cjk = len(_CJK.findall(text))
        return max(1, cjk + (len(text) - cjk) // _CHARS_PER_TOKEN)


def _tool_call_text(call: ToolCall) -> str:
    """工具调用在协议上就是一个名字加一段 JSON 参数, 两段都要发给供应商.

    以前只走 message.content: ``tool_calls`` 是 ChatMessage 上与 content 并列的另一个
    字段, 于是模型自己写进补丁的整份文件在估算里等于不存在 —— 实测两轮真实 transcript,
    漏掉的这部分占全部字符的 44% 与 45%, 而它恰恰是一个写代码的 Agent 里最大的一块.
    """
    arguments = json.dumps(dict(call.arguments), ensure_ascii=False, sort_keys=True)
    return f"{call.name}{arguments}"


def _block_text(block: ContentBlock) -> str:
    """取内容块的可估算文本。未知块类型按其 repr 长度近似（预留扩展点）。

    工具结果块单列一支: ADR-0032 之后 transcript 的绝大部分体积都在它上面, 而走 repr
    会把字段名和 provenance 一起算进去 —— 那些不发给供应商, 算上就是系统性高估, 于是
    每一轮都比实际更早触发压缩。
    """
    if isinstance(block, TextBlock):
        return block.text
    if isinstance(block, ToolResultBlock):
        return block.content
    return repr(block)
