"""模型与供应商共用的读取视图.

`/model` 管模型, `/provider` 管供应商, 但两边都要回答同一批问题: 这家供应商的密钥就绪
了吗, 现在配了哪些模型, 一个字段的当前值是什么. 各写一份的话, 同一家供应商在两条命令
下会显示成不同的可用性 —— 而两边都不会报错.
"""

from __future__ import annotations

from forgecli.application.llm import providers as provider_registry
from forgecli.domain.model.provider_spec import ProviderSpec
from forgecli.interfaces.runtime.project_runtime import ProjectRuntime
from forgecli.interfaces.tui.chooser import Option
from forgecli.shared.serialization import to_jsonable


def availability(runtime: ProjectRuntime, provider_id: str) -> str:
    """只看环境变量在不在, 不读它的值 (application/llm/availability)."""
    spec = spec_of(runtime, provider_id)
    if spec is not None and not spec.api_key_env:
        return "无需密钥"
    if runtime.availability.is_available(provider_id):
        return "已就绪"
    env = spec.api_key_env if spec is not None else ""
    return f"缺少 {env}" if env else "缺少密钥"


def spec_of(runtime: ProjectRuntime, provider_id: str) -> ProviderSpec | None:
    """内置的用注册表那一份; 用户自建的按配置现造一个等价视图."""
    builtin = provider_registry.REGISTRY.get(provider_id)
    if builtin is not None:
        return builtin
    provider = runtime.llm_config.config().provider(provider_id)
    if provider is None:
        return None
    return ProviderSpec(
        id=provider.id,
        label=provider.name or provider.id,
        default_api_base=provider.api_base,
        api_key_env=provider.api_key_env or "",
    )


def model_options(runtime: ProjectRuntime) -> list[Option]:
    return [
        Option(
            f"{provider.id}:{model.id}",
            f"{provider.id}:{model.id}",
            availability(runtime, provider.id),
        )
        for provider in runtime.llm_config.providers()
        for model in provider.models
    ]


def provider_options(runtime: ProjectRuntime, *, include_builtin: bool) -> list[Option]:
    """已配置的供应商; ``include_builtin`` 时补上还没配过的内置几家.

    添加第一个模型之前, 内置的那几家在配置文件里根本不存在 —— 不补上, 用户就得先"添加
    供应商"添加一家 Forge 本来就认识的。
    """
    configured = {provider.id for provider in runtime.llm_config.providers()}
    options = [
        Option(
            provider.id,
            provider.name or provider.id,
            availability(runtime, provider.id),
        )
        for provider in runtime.llm_config.providers()
    ]
    if include_builtin:
        options.extend(
            Option(spec.id, spec.label, "内置")
            for spec in provider_registry.REGISTRY.values()
            if spec.id not in configured
        )
    return options


def text_of(value: object) -> str:
    """字段当前值的展示形态. 只做展示, 不参与任何写入."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple):
        return ", ".join(text_of(item) for item in value)
    return str(to_jsonable(value))
