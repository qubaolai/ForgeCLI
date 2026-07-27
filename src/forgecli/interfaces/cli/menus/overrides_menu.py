"""/config 下「用途模型覆盖」子菜单（ADR-0011 §5 / §16 / §19）。

每个 origin 一行：下钻后从 llm 配置的已声明模型中选择覆盖（provider:model），
或清除覆盖回落当前模型。只做交互呈现 + 调 ModelOverridesService；校验 / 持久化
都在 service 内（覆盖必须指向已声明模型；不提供候选池或自动切换）。
"""

from __future__ import annotations

from collections.abc import Callable

from forgecli.application.interaction_ports import UserOutput
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.errors import ConfigError
from forgecli.application.llm.overrides_service import ModelOverridesService
from forgecli.application.menu import Choice, Menu
from forgecli.domain.model.model_ref import ModelRef
from forgecli.domain.model.origin import RequestOrigin


class OverridesMenu:
    def __init__(
        self,
        overrides_service: ModelOverridesService,
        llm_config_service: LlmConfigService,
        output: UserOutput,
    ) -> None:
        self._overrides = overrides_service
        self._llm = llm_config_service
        self._output = output

    def root_menu(self) -> Menu:
        rows = tuple(
            Choice(
                origin.value,
                preview=self._origin_preview(origin),
                submenu=self._origin_menu(origin),
            )
            for origin in RequestOrigin
        )
        return Menu("用途模型覆盖", rows)

    # ---- 单个 origin 的覆盖编辑 ----

    def _origin_menu(self, origin: RequestOrigin) -> Callable[[], Menu]:
        def build() -> Menu:
            rows: list[Choice] = [
                Choice(
                    f"{model.provider}:{model.id}",
                    on_select=self._set(origin, model.provider, model.id),
                )
                for model in self._llm.config().all_models()
            ]
            if not rows:
                rows.append(Choice("（暂无已配置模型，先去 /config 模型配置添加）"))
            rows.append(
                Choice("清除覆盖（回落当前模型）", on_select=self._clear(origin))
            )
            return Menu(f"覆盖 · {origin.value}", tuple(rows))

        return build

    def _set(
        self, origin: RequestOrigin, provider: str, model: str
    ) -> Callable[[], None]:
        def select() -> None:
            try:
                self._overrides.set_override(
                    origin, ModelRef(provider=provider, model=model)
                )
            except ConfigError as exc:
                self._output.print(exc.message)
                return
            self._output.print(f"已设置 {origin.value} 覆盖为 {provider}:{model}。")

        return select

    def _clear(self, origin: RequestOrigin) -> Callable[[], None]:
        def select() -> None:
            try:
                self._overrides.clear_override(origin)
            except ConfigError as exc:
                self._output.print(exc.message)
                return
            self._output.print(f"已清除 {origin.value} 的覆盖，回落当前模型。")

        return select

    # ---- preview（每次渲染重读）----

    def _origin_preview(self, origin: RequestOrigin) -> Callable[[], str]:
        def preview() -> str:
            ref = self._overrides.override_for(origin)
            return str(ref) if ref is not None else "(未设置 · 使用当前模型)"

        return preview
