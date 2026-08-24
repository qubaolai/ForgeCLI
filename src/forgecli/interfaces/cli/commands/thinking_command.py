"""当前模型的 thinking 快速查看与修改命令。"""

from __future__ import annotations

from forgecli.application.config.config_service import ConfigService
from forgecli.application.interaction_ports import UserOutput
from forgecli.application.llm.catalog_builder import build_catalog
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.errors import ConfigError, ConfigValidationError
from forgecli.application.llm.thinking_runtime import ThinkingRuntimeState
from forgecli.application.slash_commands.base import CommandHandler
from forgecli.domain.intents import SlashCommand
from forgecli.domain.model.catalog import ModelCatalogEntry
from forgecli.domain.model.model_ref import ModelRef
from forgecli.domain.model.thinking import ThinkingEffortName, ThinkingMode

_USAGE = (
    "设置思考模式: /thinking on|off \n"
    "设置思考强度: /thinking effort <等级> | /thinking on <等级>"
)


class ThinkingCommand(CommandHandler):
    """只修改当前 Forge 进程内当前模型的 thinking 设置。"""

    def __init__(
        self,
        config: ConfigService,
        llm: LlmConfigService,
        output: UserOutput,
        thinking_state: ThinkingRuntimeState,
    ) -> None:
        self._config = config
        self._llm = llm
        self._output = output
        self._thinking_state = thinking_state

    def execute(self, command: SlashCommand) -> bool:
        try:
            ref = self._current_model()
            if not command.args:
                self._output.print(self._describe(ref, changed=False))
                return False

            mode, effort = self._parse_args(command.args)
            entry = self._base_entry(ref)
            try:
                changed = self._thinking_state.update(
                    ref,
                    entry,
                    mode=mode,
                    effort=effort,
                )
            except ValueError as exc:
                raise ConfigValidationError(str(exc)) from exc
            self._output.print(self._describe(ref, changed=changed))
            return changed
        except ConfigError as exc:
            self._output.print(exc.message)
            return False

    def _current_model(self) -> ModelRef:
        ref = self._config.effective().default_model
        if ref is None:
            raise ConfigValidationError(
                "当前未设置模型；请先使用 /model 选择一个模型。"
            )
        return ref

    def _parse_args(
        self, args: tuple[str, ...]
    ) -> tuple[ThinkingMode | None, ThinkingEffortName | None]:
        normalized = tuple(item.strip().lower() for item in args)
        if len(normalized) == 1 and normalized[0] in {
            item.value for item in ThinkingMode
        }:
            return ThinkingMode(normalized[0]), None

        if len(normalized) == 2 and normalized[0] == "effort":
            return None, self._effort(normalized[1])

        if len(normalized) == 2 and normalized[0] in {
            ThinkingMode.ON.value,
        }:
            return ThinkingMode(normalized[0]), self._effort(normalized[1])

        raise ConfigValidationError(_USAGE)

    @staticmethod
    def _effort(value: str) -> ThinkingEffortName:
        try:
            return ThinkingEffortName(value)
        except ValueError as exc:
            raise ConfigValidationError(str(exc)) from exc

    def _describe(self, ref: ModelRef, *, changed: bool) -> str:
        entry = self._base_entry(ref)
        entry = self._thinking_state.apply(ref, entry)
        prefix = "已更新" if changed else "当前设置"
        return f"{prefix}：模型 {ref} · {self._thinking_text(entry)}"

    def _base_entry(self, ref: ModelRef) -> ModelCatalogEntry:
        catalog = build_catalog(self._llm.config())
        if not catalog.has_model(ref):
            raise ConfigValidationError(f"模型不存在: {ref}")
        return catalog.get(ref)

    @staticmethod
    def _thinking_text(entry: ModelCatalogEntry) -> str:
        if entry.thinking_mode is ThinkingMode.OFF:
            return "thinking off"

        effective = entry.effective_thinking_effort
        effort = effective.value if effective is not None else "默认"
        supported = " / ".join(
            item.value for item in entry.thinking_capabilities.efforts
        )
        suffix = f" · 可用强度 [{supported}]" if supported else ""
        return f"thinking {entry.thinking_mode.value}/{effort}{suffix}"
