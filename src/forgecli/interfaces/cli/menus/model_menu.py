"""模型命令菜单面板

可用模型供应商按照供应商是否配置了api-key。
只做交互呈现 + 调 ProviderAvailability / ConfigService；模型列表仍从 LLM 配置读取。
"""

from __future__ import annotations

from collections.abc import Callable

from forgecli.application.config.config_service import ConfigService
from forgecli.application.interaction_ports import UserOutput
from forgecli.application.llm import providers as provider_registry
from forgecli.application.llm.availability import EnvProviderAvailability
from forgecli.application.llm.config.llm_config import ModelSpec
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.menu import Choice, Menu
from forgecli.domain.config import config_keys
from forgecli.domain.model.model_ref import ModelRef


class ModelsMenu:
    def __init__(
        self,
        llm_config_service: LlmConfigService,
        availability: EnvProviderAvailability,
        config_service: ConfigService,
        output: UserOutput,
    ) -> None:
        self._llm_config_service = llm_config_service
        self._availability = availability
        self._config_service = config_service
        self._output = output

    def root_menu(self) -> Menu:
        rows: list[Choice] = [Choice("当前模型", preview=self._current_preview)]
        for provider_id in sorted(provider_registry.REGISTRY):
            rows.append(self._provider_choice(provider_id))
        return Menu("模型选择", tuple(rows))

    # ---- 供应商行 ----

    def _provider_choice(self, provider_id: str) -> Choice:
        spec = provider_registry.REGISTRY[provider_id]
        if not self._availability.is_available(provider_id):
            # 不可用：没有 submenu，→ 不下钻；preview 给出可操作提示。
            return Choice(
                spec.label,
                preview=self._unavailable_preview(spec.api_key_env),
            )
        if not self._models_of(provider_id):
            # 可用但用户还没在 /config 配置任何模型。
            return Choice(
                spec.label,
                preview=lambda: "可用 · 暂无已配置模型（去 /config 添加）",
            )
        return Choice(
            spec.label,
            preview=self._available_preview(provider_id),
            submenu=self._provider_models(provider_id),
        )

    def _provider_models(self, provider_id: str) -> Callable[[], Menu]:
        spec = provider_registry.REGISTRY[provider_id]

        def build() -> Menu:
            models = self._models_of(provider_id)
            rows: tuple[Choice, ...] = tuple(
                Choice(
                    model.id,
                    preview=self._model_preview(model),
                    on_select=self._select(model),
                    # 选中即切换完成, 没有第二步要做, 停在菜单里只会让人再按一次 Esc.
                    close_on_select=True,
                )
                for model in models
            )
            if not rows:
                rows = (Choice("（暂无已配置模型）"),)
            return Menu(f"{spec.label} · 选择模型", rows)

        return build

    # ---- 选中即切换 ----

    def _select(self, model: ModelSpec) -> Callable[[], None]:
        ref = ModelRef(provider=model.provider, model=model.id)

        def select() -> None:
            # 默认模型引用是两个应用级配置项，走统一 set（无专门 setter）。
            self._config_service.set(
                config_keys.DEFAULT_MODEL_PROVIDER_KEY, ref.provider
            )
            self._config_service.set(config_keys.DEFAULT_MODEL_NAME_KEY, ref.model)
            self._output.print(f"已切换默认模型为 {ref}。")

        return select

    # ---- 取数 / preview（每次渲染重读）----

    def _models_of(self, provider_id: str) -> tuple[ModelSpec, ...]:
        provider = self._llm_config_service.config().provider(provider_id)
        return provider.models if provider else ()

    def _current_preview(self) -> str:
        ref = self._config_service.effective().default_model
        if ref is None:
            return "(未设置，进入下方供应商选择)"
        # 当前默认模型可能已被从配置中删除：给出可操作提示而不是静默。
        if self._llm_config_service.config().model(ref.provider, ref.model) is None:
            return f"{ref}（已不在已配置模型中，建议重新选择）"
        return str(ref)

    def _available_preview(self, provider_id: str) -> Callable[[], str]:
        return lambda: f"可用 · {len(self._models_of(provider_id))} 个模型"

    def _unavailable_preview(self, api_key_env: str) -> Callable[[], str]:
        return lambda: f"未配置 {api_key_env}（不可选）"

    def _model_preview(self, model: ModelSpec) -> Callable[[], str]:
        def preview() -> str:
            p = model.params
            summary = f"ctx={p.context_window or '-'} max={p.max_tokens or '-'}"
            current = self._config_service.effective().default_model
            ref = ModelRef(provider=model.provider, model=model.id)
            mark = " ·(当前)" if current is not None and ref == current else ""
            return f"{summary}{mark}"

        return preview
