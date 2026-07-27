"""请求前 token 估算 TokenEstimator（ADR-0011 §11.4）。

统一的调用前输入 token 估算组件，服务于两件事：
    1. 判断输入是否超出所选模型上下文窗口（gateway 请求前预检查）。
    2. 给 BudgetGuard 做请求前预算裁决（治理件，MVP no-op）。

估算覆盖 system prompt、messages（含 tool result 文本）、tools schema。
估算只读、不写盘；近似误差不阻塞调用，但估算得出的 usage 必须标 estimated=True。

MVP 为近似实现（§17：TokenEstimator 至少要有近似实现）：按「4 字符 ≈ 1 token」
的经验比例折算，另计每条消息的固定结构开销。精确分词器（按 provider/model 选择）
留后续接入，接口不变。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod

from forgecli.domain.conversation.message import ChatMessage, ContentBlock, TextBlock
from forgecli.domain.tool.tool_call import ToolSpec

# 经验近似：平均约 4 个字符折 1 token（对中英文混排偏保守）。
_CHARS_PER_TOKEN = 4
# 每条消息的结构开销（角色标记、分隔符等）。
_PER_MESSAGE_OVERHEAD = 4


class TokenEstimator(ABC):
    """请求前输入 token 估算端口。"""

    @abstractmethod
    def estimate_input(
        self,
        *,
        messages: tuple[ChatMessage, ...],
        system_prompt: str | None = None,
        tools: tuple[ToolSpec, ...] = (),
    ) -> int:
        """估算一次请求的输入 token（messages + system prompt + tools schema）。"""

    @abstractmethod
    def estimate_text(self, text: str) -> int:
        """估算一段纯文本的 token 数（供输出估算 / 中断收尾使用）。"""


class ApproximateTokenEstimator(TokenEstimator):
    """字符比例近似估算器。确定性、无 IO，可直接用于单测。"""

    def estimate_input(
        self,
        *,
        messages: tuple[ChatMessage, ...],
        system_prompt: str | None = None,
        tools: tuple[ToolSpec, ...] = (),
    ) -> int:
        total = 0
        if system_prompt:
            total += self.estimate_text(system_prompt)
        for message in messages:
            total += _PER_MESSAGE_OVERHEAD
            for block in message.content:
                total += self.estimate_text(_block_text(block))
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
        return max(1, len(text) // _CHARS_PER_TOKEN)


def _block_text(block: ContentBlock) -> str:
    """取内容块的可估算文本。未知块类型按其 repr 长度近似（预留扩展点）。"""
    if isinstance(block, TextBlock):
        return block.text
    return repr(block)
