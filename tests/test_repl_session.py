"""REPL 会话事件记录：自然语言成对落盘 + 只记录真正写入的命令（6-27/6-29 集成）。

自然语言 -> user_message + assistant_message（同 turn）；/plan -> mode_changed；
/status（只读）不记录；/add-dir 真正新增 -> slash_command；取消则不记录（惰性零文件）。
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from rich.console import Console

from forgecli.application.agent_loop import (
    AgentLoop,
    AnswerAction,
    LoopDecision,
    LoopInput,
    LoopObservation,
    LoopStepResult,
    LoopStop,
    LoopStopReason,
)
from forgecli.application.agent_turn import AgentTurnService, TurnCancelSource
from forgecli.application.intent_router import IntentRouter
from forgecli.application.interaction_ports import DirectoryPicker
from forgecli.application.project import (
    ProjectConfig,
    ProjectContext,
    ProjectService,
)
from forgecli.application.session import EventType, SessionService
from forgecli.infrastructure.project import (
    TomlProjectConfigStore,
    TomlProjectIndexStore,
)
from forgecli.infrastructure.session import JsonlEventStore, JsonStateStore
from forgecli.interfaces.cli.menu_presenter import RichMenuPresenter
from forgecli.interfaces.cli.output import RichOutput
from forgecli.interfaces.cli.repl import Repl
from forgecli.interfaces.cli.stream_render import StreamingTranscript
from forgecli.interfaces.cli.wiring import build_registry


class _EchoLoop(AgentLoop):
    """脚本化单步 loop：固定回答，供本文件驱动 AgentTurnService。"""

    def start(self, loop_input: LoopInput) -> LoopStepResult:
        return LoopDecision(next_action=AnswerAction(text="好的"))

    def observe(self, observation: LoopObservation) -> LoopStepResult:
        return LoopStop.of(LoopStopReason.FINAL_ANSWER)


class _DirPicker(DirectoryPicker):
    def __init__(self, answer: str | None) -> None:
        self._answer = answer

    def pick(self, list_subdirs: Callable[[str], Sequence[str]]) -> str | None:
        return self._answer


def _context() -> ProjectContext:
    return ProjectContext(
        ProjectConfig(
            project_id="repo-test",
            trusted=True,
            primary_workspace_root="/work",
            workspace_roots=("/work",),
        )
    )


def _service() -> ProjectService:
    root = Path(tempfile.mkdtemp()) / "projects"
    return ProjectService(
        TomlProjectIndexStore(root / "index.toml"),
        TomlProjectConfigStore(root),
    )


def _repl(sessions: Path, picker: DirectoryPicker) -> Repl:
    console = Console(record=True)
    output = RichOutput(console)
    session = SessionService(
        JsonlEventStore(sessions),
        JsonStateStore(sessions),
        workspace_root="/work",
        id_factory=lambda: "sid",
    )
    session.start()
    agent_turn = AgentTurnService(session, loop_factory=_EchoLoop)
    registry = build_registry(
        session,
        _context(),
        _service(),
        RichMenuPresenter(console),
        picker,
        output,
        agent_turn,
    )
    router = IntentRouter(registry)
    return Repl(
        console,
        router,
        registry,
        output,
        session,
        agent_turn,
        stream_view=StreamingTranscript(console),
        cancel_source=TurnCancelSource(),
    )


def test_repl_records_conversation_pair_and_real_writes(tmp_path: Path) -> None:
    target = tmp_path / "extra"
    target.mkdir()
    sessions = tmp_path / "sessions"
    repl = _repl(sessions, _DirPicker(str(target)))

    repl._process_line("你好")  # -> user_message + assistant_message
    repl._process_line("/plan")  # -> mode_changed
    repl._process_line("/status")  # 只读：不记录
    repl._process_line("/add-dir")  # 真正新增目录：记录

    events = JsonlEventStore(sessions).read("sid")
    assert [e.type for e in events] == [
        EventType.SESSION_CREATED,
        EventType.USER_MESSAGE,
        EventType.ASSISTANT_MESSAGE,
        EventType.MODE_CHANGED,
        EventType.SLASH_COMMAND,
    ]
    assert events[-1].payload["command"] == "add-dir"


def test_repl_does_not_record_cancelled_write(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    repl = _repl(sessions, _DirPicker(None))  # 取消选择目录

    repl._process_line("/add-dir")  # 取消：无写入
    repl._process_line("/status")  # 只读

    # 没有任何真正写入 -> 惰性会话从未落盘。
    assert JsonlEventStore(sessions).read("sid") == []
