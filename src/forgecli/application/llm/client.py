"""LLM 调用切片的端口（占位，仅接口，尚未实现）。

这是为「后续 LLM 调用适配」预留的接缝。约定：
    - LlmClient 是被驱动端口；每家供应商的 adapter（将来在
      infrastructure/llm/adapters/）实现它。
    - adapter 用 provider id 作分发键，消费配置切片产出的 ProviderConfig / ModelSpec
      （base_url、timeout、采样参数、extra…）来构造真实请求——不重复定义供应商/模型。
    - 现在只放最小请求/响应原语，确保接口可编译；streaming / tool-call / token 用量等
      待真正做适配时再扩展，不在此预设。

本模块当前不被装配（wiring 未引用），不影响配置切片运行。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from forgecli.application.llm.config.model import ModelSpec, ProviderConfig


@dataclass(frozen=True)
class ChatMessage:
    """一条对话消息（最小原语）。"""

    role: str  # system / user / assistant
    content: str


@dataclass(frozen=True)
class Completion:
    """一次补全结果（最小原语）。"""

    content: str


class LlmClient(ABC):
    """LLM 调用端口；由各供应商 adapter 实现。"""

    @abstractmethod
    def complete(
        self,
        provider: ProviderConfig,
        model: ModelSpec,
        messages: Sequence[ChatMessage],
    ) -> Completion:
        """对给定供应商 / 模型发起一次补全。"""
