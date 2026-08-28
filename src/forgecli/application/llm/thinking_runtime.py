"""thinking 的进程内运行时状态（ADR-0011 §7）。"""

from __future__ import annotations

from dataclasses import dataclass, replace

from forgecli.domain.model.catalog import ModelCatalogEntry
from forgecli.domain.model.model_ref import ModelRef
from forgecli.domain.model.thinking import (
    ModelThinkingSettings,
    ThinkingEffortName,
    ThinkingMode,
)


@dataclass(frozen=True)
class _ThinkingOverride:
    """一个模型的进程内部分覆盖；None 表示沿用持久配置。"""

    mode: ThinkingMode | None = None
    effort: ThinkingEffortName | None = None


class ThinkingRuntimeState:
    """保存当前 Forge 进程的 thinking 覆盖，并负责应用模型能力校验。"""

    def __init__(self) -> None:
        self._overrides: dict[ModelRef, _ThinkingOverride] = {}

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

        previous = self._overrides.get(ref, _ThinkingOverride())
        candidate = _ThinkingOverride(
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
    def _validate(entry: ModelCatalogEntry, override: _ThinkingOverride) -> None:
        settings = ModelThinkingSettings(
            mode=override.mode or entry.thinking_mode,
            effort=override.effort
            if override.effort is not None
            else entry.thinking_effort,
        )
        entry.thinking_capabilities.validate(settings)
