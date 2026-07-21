"""当前进程内的 thinking 覆盖。

``llm.toml`` 保存模型默认值；``/thinking`` 只修改当前 Forge 进程中的覆盖，
不会改变应用级配置，也不会进入 ``ModelRequest``。覆盖按 ModelRef 隔离，
因此切换模型时自动读取新模型默认值，切回模型时仍可恢复本次进程内的修改。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from forgecli.application.llm.gateway.catalog import (
    ModelCatalogEntry,
    ModelCatalogService,
)
from forgecli.application.llm.model_ref import ModelRef
from forgecli.application.llm.thinking import (
    ModelThinkingSettings,
    ThinkingEffortName,
    ThinkingMode,
)


@dataclass(frozen=True)
class ThinkingOverride:
    """一个模型的进程内部分覆盖；None 表示沿用 llm.toml 默认值。"""

    mode: ThinkingMode | None = None
    effort: ThinkingEffortName | None = None


class ThinkingRuntimeState:
    """保存当前 Forge 进程的 thinking 覆盖，并负责应用模型能力校验。"""

    def __init__(self) -> None:
        self._overrides: dict[ModelRef, ThinkingOverride] = {}

    def update(
        self,
        ref: ModelRef,
        entry: ModelCatalogEntry,
        *,
        mode: ThinkingMode | None = None,
        effort: ThinkingEffortName | None = None,
    ) -> bool:
        """原子更新一个模型的进程内覆盖。"""

        if mode is None and effort is None:
            return False

        previous = self._overrides.get(ref, ThinkingOverride())
        candidate = ThinkingOverride(
            mode=mode if mode is not None else previous.mode,
            effort=effort if effort is not None else previous.effort,
        )
        self._validate(entry, candidate)
        if candidate == previous:
            return False
        self._overrides[ref] = candidate
        return True

    def apply(self, ref: ModelRef, entry: ModelCatalogEntry) -> ModelCatalogEntry:
        """将进程内覆盖合并到目录条目；没有覆盖时返回原条目。"""

        override = self._overrides.get(ref)
        if override is None:
            return entry

        self._validate(entry, override)
        settings = ModelThinkingSettings(
            mode=override.mode or entry.thinking_mode,
            effort=override.effort
            if override.effort is not None
            else entry.thinking_effort,
        )
        return replace(entry, thinking_settings=settings)

    @staticmethod
    def _validate(entry: ModelCatalogEntry, override: ThinkingOverride) -> None:
        settings = ModelThinkingSettings(
            mode=override.mode or entry.thinking_mode,
            effort=override.effort
            if override.effort is not None
            else entry.thinking_effort,
        )
        entry.thinking_capabilities.validate(settings)


class ThinkingOverlayCatalog(ModelCatalogService):
    """在只读模型目录上叠加当前进程的 thinking 设置。"""

    def __init__(self, base: ModelCatalogService, state: ThinkingRuntimeState) -> None:
        self._base = base
        self._state = state

    def has_model(self, ref: ModelRef) -> bool:
        return self._base.has_model(ref)

    def get(self, ref: ModelRef) -> ModelCatalogEntry:
        return self._state.apply(ref, self._base.get(ref))
