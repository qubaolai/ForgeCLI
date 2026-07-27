"""07-01 周集成（聚焦 resume 链路）：REPL + SessionService + ResumeService。

按 07-01 README「TTY 受限则以 REPL / session / resume service 集成测试为主验收」：
直接驱动 ``repl._process_line``，覆盖
    - 裸 forge 启动 session + user turn 写入事件 + title 首次落盘；
    - /status 读 state；
    - /resume <id> 恢复历史会话（不回放）后续写到同一 events.jsonl，event_id / turn_id
      续接、mode 保留。
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
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
from forgecli.application.session import (
    EventType,
    SessionEvent,
    SessionService,
    SessionSnapshot,
)
from forgecli.domain.intents import SessionMode
from forgecli.infrastructure.config import config_dir
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


class _NoPicker(DirectoryPicker):
    def pick(self, list_subdirs: Callable[[str], Sequence[str]]) -> str | None:
        return None


def _context() -> ProjectContext:
    return ProjectContext(
        ProjectConfig(
            project_id="repo-test",
            trusted=True,
            primary_workspace_root="/work",
            workspace_roots=("/work",),
        )
    )


def _project_service() -> ProjectService:
    root = Path(tempfile.mkdtemp()) / "projects"
    return ProjectService(
        TomlProjectIndexStore(root / "index.toml"),
        TomlProjectConfigStore(root),
    )


def _repl(sessions: Path) -> Repl:
    console = Console(record=True)
    output = RichOutput(console)
    session = SessionService(
        JsonlEventStore(sessions),
        JsonStateStore(sessions),
        workspace_root="/work",
        id_factory=lambda: "live-sid",
    )
    session.start()
    agent_turn = AgentTurnService(session, loop_factory=_EchoLoop)
    registry = build_registry(
        session,
        _context(),
        _project_service(),
        RichMenuPresenter(console),
        _NoPicker(),
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


def _make_history(sessions: Path, session_id: str) -> SessionSnapshot:
    events = JsonlEventStore(sessions)
    rows = [
        (EventType.SESSION_CREATED, {"workspace_root": "/work"}),
        (EventType.USER_MESSAGE, {"turn_id": "turn_0001", "text": "你好"}),
        (EventType.ASSISTANT_MESSAGE, {"turn_id": "turn_0001", "text": "在的"}),
    ]
    last = ""
    for i, (etype, payload) in enumerate(rows, start=1):
        last = f"evt_{i:04d}"
        events.append(
            SessionEvent(
                event_id=last,
                session_id=session_id,
                type=etype,
                created_at="2026-06-29T10:00:00+08:00",
                payload=payload,
            )
        )
    snapshot = SessionSnapshot(
        session_id=session_id,
        workspace_root="/work",
        mode=SessionMode.PLAN,
        last_event_id=last,
        updated_at="2026-06-29T10:00:00+08:00",
        title="你好",
    )
    JsonStateStore(sessions).write(snapshot)
    return snapshot


def _aligned_sessions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """让 config_dir() 落到 tmp，并返回 build_registry 算出的同一 sessions 目录。

    与生产一致：活动会话与 ResumeService 共用 ``projects/<project-id>/sessions``。
    project_id 取 _context() 的 ``repo-test``。
    """
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(tmp_path))
    return config_dir() / "projects" / "repo-test" / "sessions"


def test_title_set_on_first_persist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sessions = _aligned_sessions(monkeypatch, tmp_path)
    repl = _repl(sessions)

    repl._process_line("你好世界这是一句很长的话")  # 首条自然语言 -> 首次落盘

    snapshot = JsonStateStore(sessions).read("live-sid")
    assert snapshot is not None
    assert snapshot.title == "你好世界这是一句…"  # 前 8 字符 + 省略号


def test_resume_then_continue_appends_to_same_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sessions = _aligned_sessions(monkeypatch, tmp_path)
    _make_history(sessions, "hist-1")  # 3 events, evt_0003, 1 个 user_message
    repl = _repl(sessions)

    repl._process_line("/status")  # 只读当前（fresh）会话
    repl._process_line("/resume hist-1")  # 恢复历史会话（不回放）
    repl._process_line("继续聊")  # 续写：user + assistant 追加进 hist-1

    events = JsonlEventStore(sessions).read("hist-1")
    types = [e.type for e in events]
    # 历史 3 条 + 新 user/assistant 一对
    assert types == [
        EventType.SESSION_CREATED,
        EventType.USER_MESSAGE,
        EventType.ASSISTANT_MESSAGE,
        EventType.USER_MESSAGE,
        EventType.ASSISTANT_MESSAGE,
    ]
    # event_id 续接：evt_0004 / evt_0005
    assert events[3].event_id == "evt_0004"
    assert events[4].event_id == "evt_0005"
    # turn_id 续接：历史 turn_0001 -> 新 turn_0002
    assert events[3].payload["turn_id"] == "turn_0002"
    # mode 保留：恢复的会话仍是历史的 plan，续写后快照 mode 不变
    resumed_state = JsonStateStore(sessions).read("hist-1")
    assert resumed_state is not None
    assert resumed_state.mode == SessionMode.PLAN
    assert resumed_state.last_event_id == "evt_0005"
    # 续写仍属 hist-1（没有把新事件落到那个 fresh 的 live-sid 上）
    assert JsonlEventStore(sessions).read("live-sid") == []
