"""配置现读的 ModelSelectionResolver（ADR-0011 §2 / §5 运行期装配）。

当前模型（/model）、按用途覆盖（/config）与模型目录（llm.toml）都可能在会话中
被修改，因此不能在启动时把它们冻进 resolver。本类每次 resolve 时现读三个来源、
重建 DefaultModelSelectionResolver 并委托——解析语义（含 catalog 校验、无 fallback）
与 DefaultModelSelectionResolver 完全一致。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from forgecli.application.config.config_service import ConfigService
from forgecli.application.llm.catalog_builder import build_catalog
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.gateway.catalog import ModelCatalogService
from forgecli.application.llm.gateway.default_selection_resolver import (
    DefaultModelSelectionResolver,
)
from forgecli.application.llm.gateway.selection_resolver import ModelSelectionResolver
from forgecli.application.llm.thinking_runtime import (
    ThinkingOverlayCatalog,
    ThinkingRuntimeState,
)
from forgecli.domain.model.model_ref import ModelRef
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.resolved import ResolvedModel
from forgecli.domain.model.selection import ModelSelection


class ConfigBackedSelectionResolver(ModelSelectionResolver):
    """每次 resolve 现读配置（当前模型 / 覆盖表 / 目录）的动态解析器。"""

    def __init__(
        self,
        *,
        config_service: ConfigService,
        llm_config_service: LlmConfigService,
        overrides_loader: Callable[[], Mapping[RequestOrigin, ModelRef]],
        thinking_state: ThinkingRuntimeState | None = None,
    ) -> None:
        self._config = config_service
        self._llm = llm_config_service
        self._load_overrides = overrides_loader
        self._thinking_state = thinking_state

    def resolve(
        self,
        selection: ModelSelection,
        *,
        origin: RequestOrigin,
        required_capabilities: tuple[str, ...] = (),
        min_context_window: int | None = None,
    ) -> ResolvedModel:
        catalog: ModelCatalogService = build_catalog(self._llm.config())
        if self._thinking_state is not None:
            catalog = ThinkingOverlayCatalog(catalog, self._thinking_state)
        delegate = DefaultModelSelectionResolver(
            catalog,
            current_model=self._config.effective().default_model,
            overrides=self._load_overrides(),
        )
        return delegate.resolve(
            selection,
            origin=origin,
            required_capabilities=required_capabilities,
            min_context_window=min_context_window,
        )
