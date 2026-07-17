"""供应商调用 adapter（infrastructure 层）。

adapter 只做「统一请求 -> 某供应商协议」的映射，实现 application 层的 ModelProvider
接口。FakeModelProvider 为确定性测试替身；OpenAICompatibleProvider 覆盖
deepseek / mimo / openai / local 等 OpenAI-compatible 供应商（ADR-0011 §6）。
"""

from forgecli.infrastructure.llm.adapters.fake_provider import FakeModelProvider
from forgecli.infrastructure.llm.adapters.openai_compatible import (
    OpenAICompatibleProvider,
)

__all__ = ["FakeModelProvider", "OpenAICompatibleProvider"]
