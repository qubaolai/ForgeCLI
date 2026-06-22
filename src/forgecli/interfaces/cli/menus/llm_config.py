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

from forgecli.application.llm import providers as provider_registry
from forgecli.application.llm.config.model import STANDARD_FIELDS
from forgecli.application.llm.config.service import LlmConfigService
from forgecli.application.llm.errors import ConfigError
from forgecli.application.menu import Choice, Menu
from forgecli.application.ports import Output

# 供应商详情里可编辑的字段（label, key）
_PROVIDER_FIELDS = (
    ("展示名", "name"),
    ("API 地址", "api_base"),
    ("API Key 环境变量", "api_key_env"),
    ("超时(秒)", "timeout"),
    ("重试次数", "max_retries"),
)


class LlmMenu:
    def __init__(self, service: LlmConfigService, output: Output) -> None:
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
                Choice(
                    field.label,
                    preview=self._field_preview(provider_id, model_id, field.name),
                    on_text=self._set_model_field(provider_id, model_id, field.name),
                    text_default=self._field_preview(provider_id, model_id, field.name),
                )
                for field in STANDARD_FIELDS
            ]
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
            return f"ctx={p.context_window or '-'} max={p.max_tokens or '-'}"

        return preview

    def _field_preview(
        self, provider_id: str, model_id: str, field: str
    ) -> Callable[[], str]:
        def preview() -> str:
            model = self._service.config().model(provider_id, model_id)
            if model is None:
                return ""
            value = getattr(model.params, field, None)
            return "" if value is None else str(value)

        return preview

    def _extra_preview(self, provider_id: str, model_id: str) -> Callable[[], str]:
        def preview() -> str:
            model = self._service.config().model(provider_id, model_id)
            return model.params.extra_json() if model else ""

        return preview
