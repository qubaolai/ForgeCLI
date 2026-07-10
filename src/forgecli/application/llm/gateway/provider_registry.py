"""运行时 provider 分发注册表 Provider

把 provider_id 绑定到具体 ModelProvider adapter 实例，供 gateway 按 id 路由。
唯一「哪些 provider 存在」的身份权威来自 providers.REGISTRY（封闭集）；本类只在该封闭
集之上做 adapter 绑定，不引入第二套 provider 定义，从根上杜绝两套注册表漂移。

为什么单独一层、而不是把 adapter 挂到静态 ProviderSpec 上：
    adapter 实例依赖运行时配置（api_base / 超时 / 后续凭证），只能在组合根 / 测试里
    构造；塞进模块级静态 REGISTRY 会退化成可变全局单例。故绑定放这里。

准入规则：
    - provider 由代码注册（register），配置文件无法注入任意 provider 类名。
    - 注册 / 查询未知 provider 一律复用既有 UnknownProvider 明确报错。
"""

from __future__ import annotations

from forgecli.application.llm.gateway.errors import ModelUnavailableError
from forgecli.application.llm.gateway.provider import ModelProvider
from forgecli.application.llm.providers import require_known_provider


class ProviderRegistry:
    """provider_id -> ModelProvider 的运行时分发注册表（代码侧绑定）。"""

    def __init__(self) -> None:
        self._adapters: dict[str, ModelProvider] = {}

    def register(self, provider: ModelProvider) -> None:
        """绑定一个 adapter；provider_id 必须属于封闭集，且不可重复注册。"""
        require_known_provider(
            provider.provider_id
        )  # 检查供应商 未知 -> UnknownProvider
        if provider.provider_id in self._adapters:
            raise ValueError(f"provider 重复注册: {provider.provider_id!r}")
        self._adapters[provider.provider_id] = provider

    def get(self, provider_id: str) -> ModelProvider:
        """返回已经注册的 adapter 区分未知 provider 与已知但未绑定 adapter"""
        require_known_provider(provider_id)  # 检查供应商 未知 -> UnknownProvider
        adapter = self._adapters.get(provider_id)
        if adapter is None:
            raise ModelUnavailableError(
                f"provider {provider_id!r} 已知但未注册 adapter",
                provider=provider_id,
            )
        return adapter
