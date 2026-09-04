"""/provider: 供应商的添加与编辑.

从 `/model` 里分出来: 供应商是一次性配好的接入点 (地址, 协议, 密钥来源), 模型是天天要
换的那一项. 混在一个菜单里, 每次换模型都要从"添加供应商"旁边走过去.

表单字段来自 `PROVIDER_FIELDS`, 由 `ProviderConfig` 的字段声明派生 (ADR-0040 决策 4.2).
"""

from __future__ import annotations

from rich.text import Text

from forgecli.application.llm import providers as provider_registry
from forgecli.application.llm.config.llm_config import PROVIDER_FIELDS
from forgecli.domain.model.provider_spec import ProviderProtocol
from forgecli.interfaces.tui.chooser import Option, ask_text, choose
from forgecli.interfaces.tui.commands.context import CommandContext
from forgecli.interfaces.tui.commands.llm_view import (
    availability,
    provider_options,
    text_of,
)
from forgecli.interfaces.tui.console import (
    STYLE_DIM,
    error,
    listing,
    ok,
    rule,
)
from forgecli.shared.errors import ConfigValidationError

_MENU = (
    Option("add", "添加供应商", "只收 OpenAI 兼容协议"),
    Option("edit", "编辑供应商", "地址, 超时, 密钥环境变量"),
)


def cmd_provider(context: CommandContext, argument: str) -> None:
    if not context.require_idle():
        return
    raw = argument.strip()
    if raw == "add":
        _add(context)
        return
    while True:
        _overview(context)
        picked = choose(context.console, list(_MENU))
        if picked is None:
            return
        if picked.key == "add":
            _add(context)
        else:
            _edit(context)


def _overview(context: CommandContext) -> None:
    runtime = context.runtime
    rule(context.console, "供应商")
    configured = runtime.llm_config.providers()
    builtin = set(provider_registry.REGISTRY)
    if configured:
        context.console.print(
            listing(
                ("供应商", "地址", "凭证", "模型", "来源"),
                (
                    (
                        provider.id,
                        provider.api_base,
                        availability(runtime, provider.id),
                        str(len(provider.models)),
                        "内置" if provider.id in builtin else "自建",
                    )
                    for provider in configured
                ),
            )
        )
    else:
        context.console.print(
            Text("还没有配置过供应商; 内置几家在添加模型时直接可选", style=STYLE_DIM)
        )


def _add(context: CommandContext) -> None:
    console = context.console
    provider_id = ask_text(console, "供应商 id (小写字母, 数字与短横)")
    if not provider_id:
        return
    name = ask_text(console, "展示名", default=provider_id) or provider_id
    api_base = ask_text(console, "API 地址")
    if not api_base:
        return
    protocol = choose(
        console,
        [
            Option(item.value, item.label, "" if item.supported else "暂不支持")
            for item in ProviderProtocol
        ],
        current=ProviderProtocol.OPENAI_COMPATIBLE.value,
    )
    if protocol is None:
        return
    api_key_env = ask_text(console, "API Key 环境变量名", allow_empty=True) or ""
    try:
        context.runtime.llm_config.add_provider(
            provider_id,
            name=name,
            api_base=api_base,
            protocol=protocol.key,
            api_key_env=api_key_env,
        )
        context.runtime.reload_llm()
    except (ConfigValidationError, ValueError, RuntimeError) as exc:
        error(console, str(exc))
        return
    ok(console, f"已添加供应商 {provider_id}")


def _edit(context: CommandContext) -> None:
    runtime = context.runtime
    # 只列配置文件里真有的那几家: 一家还没配过的内置供应商没有可编辑的字段, 让它出现在
    # "编辑"下面, 用户改完会发现什么都没保存.
    picked = choose(context.console, provider_options(runtime, include_builtin=False))
    if picked is None:
        return
    while True:
        provider = runtime.llm_config.config().provider(picked.key)
        if provider is None:
            error(context.console, f"供应商不存在: {picked.key}")
            return
        field = choose(
            context.console,
            [
                Option(
                    item.name, item.label, text_of(getattr(provider, item.name, None))
                )
                for item in PROVIDER_FIELDS
            ],
        )
        if field is None:
            return
        value = ask_text(context.console, field.label, default=field.hint)
        if value is None:
            continue
        try:
            runtime.llm_config.set_provider_field(picked.key, field.key, value)
            runtime.reload_llm()
        except (ConfigValidationError, ValueError, RuntimeError) as exc:
            error(context.console, str(exc))
            continue
        ok(context.console, f"{picked.key}.{field.key} = {value}")
