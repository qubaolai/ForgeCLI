"""`/thinking`：当前模型的模型级开关与开放强度更新。"""

from __future__ import annotations

from pathlib import Path

from forgecli.application.config import config_keys
from forgecli.application.config.config_service import ConfigService
from forgecli.application.interaction_ports import UserOutput
from forgecli.application.llm.catalog_builder import build_catalog
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.model_ref import ModelRef
from forgecli.application.llm.thinking import ThinkingMode
from forgecli.application.llm.thinking_runtime import ThinkingRuntimeState
from forgecli.domain.intents import SlashCommand
from forgecli.infrastructure.config.toml_store import TomlConfigStore
from forgecli.infrastructure.llm import TomlLlmConfigStore
from forgecli.interfaces.cli.commands.thinking_command import ThinkingCommand


class _RecordingOutput(UserOutput):
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, message: str) -> None:
        self.lines.append(message)


def _command(
    tmp_path: Path,
) -> tuple[
    ThinkingCommand,
    ConfigService,
    LlmConfigService,
    ThinkingRuntimeState,
    _RecordingOutput,
]:
    config = ConfigService(
        TomlConfigStore(tmp_path / "config.toml"),
        TomlConfigStore(tmp_path / "forge.toml"),
    )
    llm = LlmConfigService(TomlLlmConfigStore(tmp_path / "llm.toml"))
    llm.add_model(
        "deepseek",
        "deepseek-reasoner",
        {
            "thinking_efforts": ["high", "max"],
            "thinking_default_effort": "high",
            "thinking_mode": "on",
        },
    )
    config.set(config_keys.DEFAULT_MODEL_PROVIDER_KEY, "deepseek")
    config.set(config_keys.DEFAULT_MODEL_NAME_KEY, "deepseek-reasoner")
    thinking_state = ThinkingRuntimeState()
    output = _RecordingOutput()
    return (
        ThinkingCommand(config, llm, output, thinking_state),
        config,
        llm,
        thinking_state,
        output,
    )


def _intent(*args: str) -> SlashCommand:
    tail = " ".join(args)
    raw = f"/thinking {tail}".rstrip()
    return SlashCommand(raw_text=raw, command="thinking", args=args)


def test_show_uses_model_default_effort(tmp_path: Path) -> None:
    command, _, _, _, output = _command(tmp_path)

    assert command.execute(_intent()) is False
    assert output.lines == [
        "当前设置：模型 deepseek:deepseek-reasoner · "
        "thinking on/high · 可用强度 [high / max]"
    ]


def test_mode_and_open_effort_are_updated_atomically(tmp_path: Path) -> None:
    command, _, llm, state, output = _command(tmp_path)
    before = (tmp_path / "llm.toml").read_text(encoding="utf-8")

    assert command.execute(_intent("on", "max")) is True
    assert (tmp_path / "llm.toml").read_text(encoding="utf-8") == before
    params = llm.config().model("deepseek", "deepseek-reasoner").params
    assert params.thinking_mode.value == "on"
    assert params.thinking_effort is None
    assert (
        state.apply(
            ModelRef("deepseek", "deepseek-reasoner"),
            build_catalog(llm.config()).get(ModelRef("deepseek", "deepseek-reasoner")),
        ).thinking_mode
        is ThinkingMode.ON
    )
    assert "thinking on/max" in output.lines[-1]


def test_off_preserves_previous_effort(tmp_path: Path) -> None:
    command, _, llm, state, output = _command(tmp_path)
    command.execute(_intent("effort", "max"))

    assert command.execute(_intent("off")) is True
    params = llm.config().model("deepseek", "deepseek-reasoner").params
    assert params.thinking_mode.value == "on"
    assert params.thinking_effort is None
    ref = ModelRef("deepseek", "deepseek-reasoner")
    entry = state.apply(ref, build_catalog(llm.config()).get(ref))
    assert entry.thinking_mode is ThinkingMode.OFF
    assert entry.thinking_effort.value == "max"
    assert output.lines[-1].endswith("thinking off")


def test_unsupported_effort_is_rejected_without_partial_write(tmp_path: Path) -> None:
    command, _, llm, _, output = _command(tmp_path)

    assert command.execute(_intent("on", "xhigh")) is False
    params = llm.config().model("deepseek", "deepseek-reasoner").params
    assert params.thinking_mode is ThinkingMode.ON
    assert params.thinking_effort is None
    assert "可选值 [high / max]" in output.lines[-1]


def test_missing_current_model_has_actionable_message(tmp_path: Path) -> None:
    config = ConfigService(
        TomlConfigStore(tmp_path / "config.toml"),
        TomlConfigStore(tmp_path / "forge.toml"),
    )
    llm = LlmConfigService(TomlLlmConfigStore(tmp_path / "llm.toml"))
    output = _RecordingOutput()
    command = ThinkingCommand(config, llm, output, ThinkingRuntimeState())

    assert command.execute(_intent("on")) is False
    assert output.lines == ["当前未设置模型；请先使用 /model 选择一个模型。"]
