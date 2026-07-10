"""供应商调用 adapter（infrastructure 层）。

adapter 只做「统一请求 -> 某供应商协议」的映射，实现 application 层的 ModelProvider
接口。FakeModelProvider 为确定性测试替身；真实 provider（openai_compatible 等）
在后续切片落地。
"""

from forgecli.infrastructure.llm.adapters.fake_provider import FakeModelProvider

__all__ = ["FakeModelProvider"]
