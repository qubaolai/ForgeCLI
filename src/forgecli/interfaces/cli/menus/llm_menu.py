"""/config 下「供应商配置」与「模型配置」两棵子菜单的构建器。

只做交互呈现 + 调 LlmConfigService；校验 / 持久化都在 service 内。
菜单是动态的（模型可增删），靠 presenter 的"构建器栈每次渲染重建"实现就地刷新。

结构：
    供应商配置 → [deepseek / mimo …封闭列表]
                  → 编辑 name / api_base / api_key_env / timeout / max_retries
    模型配置   → [deepseek / mimo …] → [模型列表 + 添加 + 删除]
                                          模型 → 编辑标准字段 + 扩展字段(JSON)
"""

from __future__ import annotations

from collections.abc import Callable

from forgecli.application.interaction_ports import UserOutput
from forgecli.application.llm import providers as provider_registry
from forgecli.application.llm.catalog_builder import build_catalog
from forgecli.application.llm.config.llm_config import STANDARD_FIELDS, StandardField
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.errors import ConfigError
from forgecli.application.llm.gateway.catalog import ModelCatalogEntry
from forgecli.application.llm.model_ref import ModelRef
from forgecli.application.llm.thinking import ThinkingMode
from forgecli.application.menu import Choice, Menu

# 供应商详情里可编辑的字段（label, key）
_PROVIDER_FIELDS = (
    ("展示名", "name"),
    ("API 地址", "api_base"),
    ("API Key 环境变量", "api_key_env"),
    ("超时(秒)", "timeout"),
    ("重试次数", "max_retries"),
)


class LlmMenu:
    def __init__(self, service: LlmConfigService, output: UserOutput) -> None:
        self._service = service
        self._output = output

    # ---- 供应商配置 ----

    def providers_menu(self) -> Menu:
        choices = tuple(
            Choice(
                spec.label,
                preview=self._provider_preview(pid),
                submenu=self._provider_detail(pid),
            )
            for pid, spec in sorted(provider_registry.REGISTRY.items())
        )
        return Menu("供应商配置", choices)

    def _provider_detail(self, provider_id: str) -> Callable[[], Menu]:
        def build() -> Menu:
            rows = tuple(
                Choice(
                    label,
                    preview=self._provider_field_preview(provider_id, key),
                    on_text=self._set_provider_field(provider_id, key),
                    text_default=self._provider_field_raw(provider_id, key),
                )
                for label, key in _PROVIDER_FIELDS
            )
            return Menu(f"供应商 · {provider_id}", rows)

        return build

    # ---- 模型配置 ----

    def models_menu(self) -> Menu:
        choices = tuple(
            Choice(
                spec.label,
                preview=self._model_count_preview(pid),
                submenu=self._provider_models(pid),
            )
            for pid, spec in sorted(provider_registry.REGISTRY.items())
        )
        return Menu("模型配置", choices)

    def _provider_models(self, provider_id: str) -> Callable[[], Menu]:
        def build() -> Menu:
            provider = self._service.config().provider(provider_id)
            models = provider.models if provider else ()
            rows: list[Choice] = [
                Choice(
                    model.id,
                    preview=self._model_summary(provider_id, model.id),
                    submenu=self._model_detail(provider_id, model.id),
                )
                for model in models
            ]
            rows.append(
                Choice(
                    "添加模型",
                    on_text=self._add_model(provider_id),
                )
            )
            rows.append(Choice("删除模型", submenu=self._delete_menu(provider_id)))
            return Menu(f"模型 · {provider_id}", tuple(rows))

        return build

    def _model_detail(self, provider_id: str, model_id: str) -> Callable[[], Menu]:
        def build() -> Menu:
            model = self._service.config().model(provider_id, model_id)
            if model is None:
                return Menu(model_id, (Choice("（模型不存在 / 已删除），Esc 返回"),))
            rows = [
                self._model_field_choice(provider_id, model_id, field)
                for field in STANDARD_FIELDS
            ]
            rows.extend(
                (
                    Choice(
                        "Thinking 支持强度（逗号分隔）",
                        preview=self._thinking_efforts_preview(provider_id, model_id),
                        on_text=self._set_thinking_efforts(provider_id, model_id),
                        text_default=self._thinking_efforts_raw(provider_id, model_id),
                    ),
                    Choice(
                        "Thinking 默认强度",
                        preview=self._thinking_default_preview(provider_id, model_id),
                        on_text=self._set_thinking_default(provider_id, model_id),
                        text_default=self._thinking_default_raw(provider_id, model_id),
                    ),
                    Choice(
                        "Thinking 模式",
                        preview=self._thinking_mode_preview(provider_id, model_id),
                        on_cycle=self._cycle_thinking_mode(provider_id, model_id),
                    ),
                )
            )
            rows.append(
                Choice(
                    "扩展字段(JSON)",
                    preview=self._extra_preview(provider_id, model_id),
                    on_text=self._set_model_extra(provider_id, model_id),
                    text_default=self._extra_preview(provider_id, model_id),
                )
            )
            return Menu(f"{provider_id} / {model_id}", tuple(rows))

        return build

    def _delete_menu(self, provider_id: str) -> Callable[[], Menu]:
        def build() -> Menu:
            provider = self._service.config().provider(provider_id)
            models = provider.models if provider else ()
            rows = tuple(
                Choice(
                    f"删除 {model.id}",
                    on_select=self._remove_model(provider_id, model.id),
                )
                for model in models
            )
            if not rows:
                rows = (Choice("（暂无模型）"),)
            return Menu(f"删除模型 · {provider_id}", rows)

        return build

    # ---- 回调工厂（全部委托 service，并把业务错误转一行提示）----

    def _set_provider_field(
        self, provider_id: str, field: str
    ) -> Callable[[str], None]:
        def submit(value: str) -> None:
            self._safe(
                lambda: self._service.set_provider_field(provider_id, field, value)
            )

        return submit

    def _add_model(self, provider_id: str) -> Callable[[str], None]:
        def submit(model_id: str) -> None:
            if model_id.strip():
                self._safe(
                    lambda: self._service.add_model(provider_id, model_id.strip(), {})
                )

        return submit

    def _set_model_field(
        self, provider_id: str, model_id: str, field: str
    ) -> Callable[[str], None]:
        def submit(value: str) -> None:
            self._safe(
                lambda: self._service.set_model_field(
                    provider_id, model_id, field, value
                )
            )

        return submit

    def _cycle_model_field(
        self,
        provider_id: str,
        model_id: str,
        field: StandardField,
    ) -> Callable[[int], None]:
        def cycle(delta: int) -> None:
            current = self._field_preview(provider_id, model_id, field.name)()
            index = field.choices.index(current) if current in field.choices else 0
            value = field.choices[(index + delta) % len(field.choices)]
            self._safe(
                lambda: self._service.set_model_field(
                    provider_id, model_id, field.name, value
                )
            )

        return cycle

    def _set_model_extra(
        self, provider_id: str, model_id: str
    ) -> Callable[[str], None]:
        def submit(value: str) -> None:
            self._safe(
                lambda: self._service.set_model_extra(provider_id, model_id, value)
            )

        return submit

    def _remove_model(self, provider_id: str, model_id: str) -> Callable[[], None]:
        return lambda: self._safe(
            lambda: self._service.remove_model(provider_id, model_id)
        )

    def _safe(self, action: Callable[[], None]) -> None:
        try:
            action()
        except ConfigError as exc:
            self._output.print(exc.message)

    def _set_thinking_efforts(
        self, provider_id: str, model_id: str
    ) -> Callable[[str], None]:
        def submit(value: str) -> None:
            def update() -> None:
                self._service.set_model_thinking_efforts(provider_id, model_id, value)

            self._safe(update)

        return submit

    def _set_thinking_default(
        self, provider_id: str, model_id: str
    ) -> Callable[[str], None]:
        def submit(value: str) -> None:
            def update() -> None:
                self._service.set_model_thinking_default_effort(
                    provider_id, model_id, value
                )

            self._safe(update)

        return submit

    def _cycle_thinking_mode(
        self, provider_id: str, model_id: str
    ) -> Callable[[int], None]:
        modes = tuple(ThinkingMode)

        def cycle(delta: int) -> None:
            entry = self._catalog_entry(provider_id, model_id)
            current = entry.thinking_mode if entry is not None else ThinkingMode.OFF
            index = modes.index(current)
            target = modes[(index + delta) % len(modes)]

            def update() -> None:
                self._service.update_model_thinking(provider_id, model_id, mode=target)

            self._safe(update)

        return cycle

    # ---- preview 取值 ----

    def _provider_preview(self, provider_id: str) -> Callable[[], str]:
        return lambda: self._provider_field_value(provider_id, "api_base") or "(未设置)"

    def _provider_field_preview(
        self, provider_id: str, field: str
    ) -> Callable[[], str]:
        return lambda: self._provider_field_value(provider_id, field) or "(默认)"

    def _provider_field_raw(self, provider_id: str, field: str) -> Callable[[], str]:
        return lambda: self._provider_field_value(provider_id, field)

    def _provider_field_value(self, provider_id: str, field: str) -> str:
        provider = self._service.config().provider(provider_id)
        spec = provider_registry.REGISTRY[provider_id]
        if provider is None:
            return {
                "name": spec.label,
                "api_base": spec.default_api_base,
                "api_key_env": spec.api_key_env,
            }.get(field, "")
        value = {
            "name": provider.name,
            "api_base": provider.api_base,
            "api_key_env": provider.api_key_env or "",
            "timeout": str(provider.timeout),
            "max_retries": str(provider.max_retries),
        }.get(field, "")
        return value

    def _model_count_preview(self, provider_id: str) -> Callable[[], str]:
        def preview() -> str:
            provider = self._service.config().provider(provider_id)
            return f"{len(provider.models) if provider else 0} 个模型"

        return preview

    def _model_summary(self, provider_id: str, model_id: str) -> Callable[[], str]:
        def preview() -> str:
            model = self._service.config().model(provider_id, model_id)
            if model is None:
                return ""
            p = model.params
            return (
                f"ctx={p.context_window or '-'} max={p.max_tokens or '-'} "
                f"{self._thinking_preview(provider_id, model_id)()}"
            )

        return preview

    def _field_preview(
        self, provider_id: str, model_id: str, field: str
    ) -> Callable[[], str]:
        def preview() -> str:
            model = self._service.config().model(provider_id, model_id)
            if model is None:
                return ""
            value = getattr(model.params, field, None)
            enum_value = getattr(value, "value", None)
            if isinstance(enum_value, str):
                return enum_value
            return "" if value is None else str(value)

        return preview

    def _thinking_preview(self, provider_id: str, model_id: str) -> Callable[[], str]:
        def preview() -> str:
            entry = self._catalog_entry(provider_id, model_id)
            if entry is None:
                return "thinking=未配置"
            if entry.thinking_mode is ThinkingMode.OFF:
                return "thinking=off"
            effort = entry.effective_thinking_effort
            effort_text = effort.value if effort is not None else "默认"
            return f"thinking={entry.thinking_mode.value}/{effort_text}"

        return preview

    def _thinking_efforts_preview(
        self, provider_id: str, model_id: str
    ) -> Callable[[], str]:
        def preview() -> str:
            entry = self._catalog_entry(provider_id, model_id)
            if entry is None:
                return "（未配置）"
            efforts = entry.thinking_capabilities.efforts
            return " / ".join(item.value for item in efforts) or "（无可调强度）"

        return preview

    def _thinking_efforts_raw(
        self, provider_id: str, model_id: str
    ) -> Callable[[], str]:
        def preview() -> str:
            entry = self._catalog_entry(provider_id, model_id)
            if entry is None:
                return ""
            return ", ".join(item.value for item in entry.thinking_capabilities.efforts)

        return preview

    def _thinking_default_preview(
        self, provider_id: str, model_id: str
    ) -> Callable[[], str]:
        def preview() -> str:
            raw = self._thinking_default_raw(provider_id, model_id)()
            return raw or "（无）"

        return preview

    def _thinking_default_raw(
        self, provider_id: str, model_id: str
    ) -> Callable[[], str]:
        def preview() -> str:
            entry = self._catalog_entry(provider_id, model_id)
            if entry is None or entry.thinking_capabilities.default_effort is None:
                return ""
            return entry.thinking_capabilities.default_effort.value

        return preview

    def _thinking_mode_preview(
        self, provider_id: str, model_id: str
    ) -> Callable[[], str]:
        def preview() -> str:
            entry = self._catalog_entry(provider_id, model_id)
            return entry.thinking_mode.value if entry is not None else "off"

        return preview

    def _catalog_entry(
        self, provider_id: str, model_id: str
    ) -> ModelCatalogEntry | None:
        catalog = build_catalog(self._service.config())
        ref = ModelRef(provider=provider_id, model=model_id)
        return catalog.get(ref) if catalog.has_model(ref) else None

    def _model_field_choice(
        self,
        provider_id: str,
        model_id: str,
        field: StandardField,
    ) -> Choice:
        preview = self._field_preview(provider_id, model_id, field.name)
        if field.choices:
            return Choice(
                field.label,
                preview=preview,
                on_cycle=self._cycle_model_field(provider_id, model_id, field),
            )
        return Choice(
            field.label,
            preview=preview,
            on_text=self._set_model_field(provider_id, model_id, field.name),
            text_default=preview,
        )

    def _extra_preview(self, provider_id: str, model_id: str) -> Callable[[], str]:
        def preview() -> str:
            model = self._service.config().model(provider_id, model_id)
            return model.params.extra_json() if model else ""

        return preview
