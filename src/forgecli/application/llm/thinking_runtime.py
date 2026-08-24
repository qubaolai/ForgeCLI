"""thinking 的进程内运行时状态与目录覆盖层（ADR-0011 §7）。

覆盖值 ThinkingOverride 住在 domain.model.thinking_override；这里两个类都是运行时
构件：ThinkingRuntimeState 持有可变的进程内字典（/thinking 改了不落盘），
ThinkingOverlayCatalog 是套在目录读端口外的装饰器。
"""

from __future__ import annotations

from dataclasses import replace

from forgecli.application.llm.catalog import ModelCatalogService
from forgecli.domain.model.catalog import ModelCatalogEntry
from forgecli.domain.model.model_ref import ModelRef
from forgecli.domain.model.thinking import (
    ModelThinkingSettings,
    ThinkingEffortName,
    ThinkingMode,
)
from forgecli.domain.model.thinking_override import ThinkingOverride


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
