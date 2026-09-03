"""/model, /thinking, /gateway: 模型选择, 模型与供应商配置, 网关运行时.

表单字段不在这里写死. 模型标准字段来自 ``STANDARD_FIELDS``, 供应商字段来自
``PROVIDER_FIELDS``, 网关运行时字段来自那三个 dataclass 的字段声明 —— 都由声明派生
(ADR-0040 决策 4.2). 手抄一份的话, 加一个字段不会有任何东西报错, 只会让它在终端里
编不了.
"""

from __future__ import annotations

from rich.text import Text

from forgecli.application.llm import providers as provider_registry
from forgecli.application.llm.config.llm_config import (
    PROVIDER_FIELDS,
    STANDARD_FIELDS,
    ProviderConfig,
)
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.provider_spec import ProviderProtocol, ProviderSpec
from forgecli.domain.model.thinking import ThinkingEffortName, ThinkingMode
from forgecli.interfaces.runtime.project_runtime import ProjectRuntime
from forgecli.interfaces.tui.chooser import Option, ask_text, choose, confirm
from forgecli.interfaces.tui.commands.context import CommandContext
from forgecli.interfaces.tui.console import (
    STYLE_DIM,
    error,
    kv_table,
    listing,
    ok,
    rule,
    warn,
)
from forgecli.shared.errors import ConfigValidationError
from forgecli.shared.serialization import to_jsonable

# 模型的三个 thinking 字段不在 STANDARD_FIELDS 里: 它们各有各的写入方法 (取值范围要
# 按模型声明的强度表校验), 所以单列.
_THINKING_FIELDS = (
    ("thinking_mode", "thinking 开关 (on/off)"),
    ("thinking_effort", "thinking 默认强度"),
    ("thinking_efforts", "支持的强度 (逗号分隔)"),
    ("thinking_default_effort", "缺省强度"),
)
_GATEWAY_SECTIONS = ("cache", "circuit_breaker", "retry")

_MENU = (
    Option("current", "切换当前模型", "所有用途的默认模型"),
    Option("override", "按用途覆盖", "给某一类调用单独绑一个模型"),
    Option("thinking", "thinking", "当前模型的思考开关与强度; 只改本进程"),
    Option("add_model", "添加模型", ""),
    Option("edit_model", "编辑模型参数", ""),
    Option("remove_model", "删除模型", ""),
    Option("add_provider", "添加供应商", "只收 OpenAI 兼容协议"),
    Option("edit_provider", "编辑供应商", ""),
    Option("gateway", "网关运行时", "缓存, 熔断与重试"),
)


def cmd_model(context: CommandContext, argument: str) -> None:
    if not context.require_idle():
        return
    raw = argument.strip()
    if raw:
        provider, _, model = raw.partition(":")
        if not model:
            error(context.console, "写成 provider:model, 例如 deepseek:deepseek-chat")
            return
        _set_current(context, provider, model)
        return
    _overview(context)
    picked = choose(context.console, "要做什么", list(_MENU))
    if picked is None:
        return
    handlers = {
        "current": _pick_current,
        "override": _overrides,
        "thinking": cmd_thinking,
        "add_model": _add_model,
        "edit_model": _edit_model,
        "remove_model": _remove_model,
        "add_provider": _add_provider,
        "edit_provider": _edit_provider,
        "gateway": cmd_gateway,
    }
    handlers[picked.key](context, "")


def cmd_thinking(context: CommandContext, argument: str) -> None:
    """当前模型的 thinking. 只改本进程, 不落盘 —— 与模型配置里的持久 thinking 分开."""
    if not context.require_idle():
        return
    runtime = context.runtime
    view = runtime.thinking_view()
    if not view.get("configured"):
        warn(context.console, "当前没有可用模型; 先用 /model 选一个")
        return
    console = context.console
    console.print(
        Text(
            f"{view.get('model', '')} · 当前 {view.get('mode', '')}"
            f" / {view.get('effort', '') or '-'}",
            style=STYLE_DIM,
        )
    )
    efforts = [str(item) for item in _as_list(view.get("supported_efforts"))]
    raw = argument.strip()
    if raw:
        mode, effort = (raw, "") if raw in ("on", "off") else ("", raw)
        _apply_thinking(context, mode, effort)
        return
    picked = choose(
        console,
        "thinking",
        [Option("on", "开"), Option("off", "关")],
        current=str(view.get("mode", "")),
    )
    if picked is None:
        return
    effort = ""
    if picked.key == "on" and efforts:
        chosen = choose(
            console,
            "强度",
            [Option(item, item) for item in efforts],
            current=str(view.get("effort", "")),
        )
        if chosen is None:
            return
        effort = chosen.key
    _apply_thinking(context, picked.key, effort)


def cmd_gateway(context: CommandContext, _argument: str) -> None:
    """网关运行时: 缓存, 熔断, 重试.

    字段清单从那三个 settings dataclass 的当前取值派生, 不在这里手抄一份 —— 抄的那
    一份漂了不会报错, 只会让新加的开关在终端里编不了.
    """
    if not context.require_idle():
        return
    runtime = context.runtime
    rows: list[tuple[str, str]] = []
    for section, settings in zip(
        _GATEWAY_SECTIONS, runtime.llm_config.runtime_settings_snapshot(), strict=True
    ):
        payload = to_jsonable(settings)
        if not isinstance(payload, dict):
            continue
        rows.extend(
            (f"{section}.{key}", _text(value)) for key, value in payload.items()
        )
    rule(context.console, "网关运行时")
    context.console.print(kv_table(rows))
    picked = choose(
        context.console,
        "改哪一项",
        [Option(name, name, value) for name, value in rows],
    )
    if picked is None:
        return
    section_name, _, field_name = picked.key.partition(".")
    current = next(value for name, value in rows if name == picked.key)
    value = ask_text(context.console, picked.key, default=current)
    if value is None:
        return
    try:
        runtime.llm_config.set_runtime_field(section_name, field_name, value)
        runtime.reload_llm()
    except (ConfigValidationError, ValueError, RuntimeError) as exc:
        error(context.console, str(exc))
        return
    ok(context.console, f"{picked.key} = {value}")


# ---- 概览 ----


def _overview(context: CommandContext) -> None:
    runtime = context.runtime
    current = runtime.current_model()
    rule(context.console, "模型")
    rows: list[tuple[str, str, str, str]] = []
    for provider in runtime.llm_config.providers():
        for model in provider.models:
            ref = f"{provider.id}:{model.id}"
            rows.append(
                (
                    ("● " if ref == str(current) else "  ") + ref,
                    provider.name or provider.id,
                    _availability(runtime, provider.id),
                    _thinking_summary(model.params),
                )
            )
    if rows:
        context.console.print(listing(("模型", "供应商", "凭证", "thinking"), rows))
    else:
        context.console.print(Text("还没有配置任何模型", style=STYLE_DIM))
    overrides = runtime.model_overrides()
    if overrides:
        context.console.print()
        context.console.print(
            kv_table([(origin, ref) for origin, ref in sorted(overrides.items())])
        )


def _availability(runtime: ProjectRuntime, provider_id: str) -> str:
    """只看环境变量在不在, 不读它的值 (application/llm/availability)."""
    spec = _spec_of(runtime, provider_id)
    if spec is not None and not spec.api_key_env:
        return "无需密钥"
    if runtime.availability.is_available(provider_id):
        return "已就绪"
    env = spec.api_key_env if spec is not None else ""
    return f"缺少 {env}" if env else "缺少密钥"


def _spec_of(runtime: ProjectRuntime, provider_id: str) -> ProviderSpec | None:
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


def _thinking_summary(params: object) -> str:
    mode = getattr(params, "thinking_mode", None)
    effort = getattr(params, "thinking_effort", None)
    if mode is None:
        return "-"
    return f"{_text(mode)}" + (f" / {_text(effort)}" if effort is not None else "")


# ---- 当前模型与覆盖 ----


def _pick_current(context: CommandContext, _argument: str) -> None:
    runtime = context.runtime
    options = _model_options(context)
    if not options:
        warn(context.console, "还没有配置任何模型; 先用 /model 菜单里的添加模型")
        return
    picked = choose(
        context.console, "当前模型", options, current=str(runtime.current_model() or "")
    )
    if picked is None:
        return
    provider, _, model = picked.key.partition(":")
    _set_current(context, provider, model)


def _set_current(context: CommandContext, provider: str, model: str) -> None:
    try:
        context.runtime.set_current_model(provider, model)
    except (ValueError, RuntimeError) as exc:
        error(context.console, str(exc))
        return
    ok(context.console, f"当前模型: {provider}:{model}")


def _overrides(context: CommandContext, _argument: str) -> None:
    runtime = context.runtime
    current = runtime.model_overrides()
    origin = choose(
        context.console,
        "给哪个用途绑模型",
        [
            Option(item.value, item.value, current.get(item.value, "跟随当前模型"))
            for item in RequestOrigin
        ],
    )
    if origin is None:
        return
    options = [*_model_options(context), Option("", "清除覆盖", "回到当前模型")]
    picked = choose(
        context.console,
        f"{origin.key} 用哪个模型",
        options,
        current=current.get(origin.key, ""),
    )
    if picked is None:
        return
    try:
        if picked.key:
            provider, _, model = picked.key.partition(":")
            runtime.set_model_override(origin.key, provider, model)
        else:
            runtime.clear_model_override(origin.key)
    except (ValueError, RuntimeError) as exc:
        error(context.console, str(exc))
        return
    ok(context.console, f"{origin.key}: {picked.key or '跟随当前模型'}")


def _model_options(context: CommandContext) -> list[Option]:
    runtime = context.runtime
    return [
        Option(
            f"{provider.id}:{model.id}",
            f"{provider.id}:{model.id}",
            _availability(runtime, provider.id),
        )
        for provider in runtime.llm_config.providers()
        for model in provider.models
    ]


def _apply_thinking(context: CommandContext, mode: str, effort: str) -> None:
    try:
        changed = context.runtime.update_thinking(mode, effort)
    except ValueError as exc:
        error(context.console, str(exc))
        return
    view = context.runtime.thinking_view()
    label = f"{view.get('mode', '')} / {view.get('effort', '') or '-'}"
    ok(context.console, f"thinking: {label}" if changed else f"未变化: {label}")


# ---- 模型与供应商配置 ----


def _add_model(context: CommandContext, _argument: str) -> None:
    runtime = context.runtime
    provider = _pick_provider(context, "把模型加到哪家供应商下")
    if provider is None:
        return
    model_id = ask_text(context.console, "模型 id")
    if not model_id:
        return
    try:
        runtime.llm_config.add_model(provider, model_id, {})
        runtime.reload_llm()
    except (ConfigValidationError, ValueError, RuntimeError) as exc:
        error(context.console, str(exc))
        return
    ok(context.console, f"已添加 {provider}:{model_id}")
    if confirm(context.console, "设为当前模型?", default=True):
        _set_current(context, provider, model_id)


def _remove_model(context: CommandContext, _argument: str) -> None:
    runtime = context.runtime
    picked = choose(context.console, "删除哪个模型", _model_options(context))
    if picked is None:
        return
    if not confirm(context.console, f"确认删除 {picked.key}?"):
        return
    provider, _, model = picked.key.partition(":")
    try:
        runtime.llm_config.remove_model(provider, model)
        runtime.reload_llm()
    except (ConfigValidationError, ValueError, RuntimeError) as exc:
        error(context.console, str(exc))
        return
    ok(context.console, f"已删除 {picked.key}")


def _edit_model(context: CommandContext, _argument: str) -> None:
    runtime = context.runtime
    target = choose(context.console, "编辑哪个模型", _model_options(context))
    if target is None:
        return
    provider_id, _, model_id = target.key.partition(":")
    spec = runtime.llm_config.config().model(provider_id, model_id)
    if spec is None:
        error(context.console, f"模型不存在: {target.key}")
        return
    options = [
        Option(field.name, field.label, _text(getattr(spec.params, field.name, None)))
        for field in STANDARD_FIELDS
    ]
    options.extend(Option(name, label) for name, label in _THINKING_FIELDS)
    options.append(Option("extra", "厂商自定义参数 (JSON)"))
    field = choose(context.console, f"{target.key} 改哪一项", options)
    if field is None:
        return
    value = ask_text(context.console, field.label, default=field.hint, allow_empty=True)
    if value is None:
        return
    try:
        _write_model_field(context, provider_id, model_id, field.key, value)
        runtime.reload_llm()
    except (ConfigValidationError, ValueError, RuntimeError) as exc:
        error(context.console, str(exc))
        return
    ok(context.console, f"{target.key}.{field.key} = {value}")


def _write_model_field(
    context: CommandContext, provider_id: str, model_id: str, field: str, value: str
) -> None:
    """写入分支与 Web 的 ``PATCH /api/v1/models/{field}`` 一一对应."""
    service = context.runtime.llm_config
    if field == "extra":
        service.set_model_extra(provider_id, model_id, value)
    elif field == "thinking_mode":
        service.update_model_thinking(provider_id, model_id, mode=ThinkingMode(value))
    elif field == "thinking_effort":
        service.update_model_thinking(
            provider_id, model_id, effort=ThinkingEffortName(value)
        )
    elif field == "thinking_efforts":
        service.set_model_thinking_efforts(provider_id, model_id, value)
    elif field == "thinking_default_effort":
        service.set_model_thinking_default_effort(provider_id, model_id, value)
    else:
        service.set_model_field(provider_id, model_id, field, value)


def _add_provider(context: CommandContext, _argument: str) -> None:
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
        "端点协议",
        [
            Option(
                item.value,
                item.label,
                "" if item.supported else "暂不支持",
            )
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


def _edit_provider(context: CommandContext, _argument: str) -> None:
    provider_id = _pick_provider(context, "编辑哪家供应商")
    if provider_id is None:
        return
    provider = context.runtime.llm_config.config().provider(provider_id)
    if provider is None:
        error(context.console, f"供应商不存在: {provider_id}")
        return
    field = choose(
        context.console,
        f"{provider_id} 改哪一项",
        [
            Option(item.name, item.label, _provider_value(provider, item.name))
            for item in PROVIDER_FIELDS
        ],
    )
    if field is None:
        return
    value = ask_text(context.console, field.label, default=field.hint)
    if value is None:
        return
    try:
        context.runtime.llm_config.set_provider_field(provider_id, field.key, value)
        context.runtime.reload_llm()
    except (ConfigValidationError, ValueError, RuntimeError) as exc:
        error(context.console, str(exc))
        return
    ok(context.console, f"{provider_id}.{field.key} = {value}")


def _pick_provider(context: CommandContext, title: str) -> str | None:
    runtime = context.runtime
    configured = {provider.id for provider in runtime.llm_config.providers()}
    options = [
        Option(
            provider.id,
            provider.name or provider.id,
            _availability(runtime, provider.id),
        )
        for provider in runtime.llm_config.providers()
    ]
    # 内置但还没配过的也列出来: 添加第一个模型之前, 那几家在配置文件里根本不存在.
    options.extend(
        Option(spec.id, spec.label, "内置")
        for spec in provider_registry.REGISTRY.values()
        if spec.id not in configured
    )
    picked = choose(context.console, title, options)
    return None if picked is None else picked.key


def _provider_value(provider: ProviderConfig, field: str) -> str:
    return _text(getattr(provider, field, None))


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple):
        return ", ".join(_text(item) for item in value)
    rendered = to_jsonable(value)
    return str(rendered)


def _as_list(raw: object) -> list[object]:
    return list(raw) if isinstance(raw, list) else []
