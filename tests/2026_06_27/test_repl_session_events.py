"""2026-06-27：REPL 分派写入会话事件。"""

from __future__ import annotations

from rich.console import Console

from forgecli.application.intent_router import IntentRouter
from forgecli.application.interaction_ports import UserOutput
from forgecli.application.session import (
    EventStore,
    SessionEvent,
    SessionService,
    SessionSnapshot,
    SessionState,
    StateStore,
)
from forgecli.application.slash_commands import CommandHandler, CommandRegistry
from forgecli.application.slash_commands.registry import CommandCategory, CommandSpec
from forgecli.domain.intents import IntentKind, SessionMode, SlashCommand
from forgecli.interfaces.cli.output import RichOutput
from forgecli.interfaces.cli.repl import Repl


class _MemoryEventStore(EventStore):
    def __init__(self) -> None:
        self.events: list[SessionEvent] = []

    def append(self, event: SessionEvent) -> None:
        self.events.append(event)

    def read(self, session_id: str) -> list[SessionEvent]:
        return [event for event in self.events if event.session_id == session_id]


class _MemoryStateStore(StateStore):
    def __init__(self) -> None:
        self.snapshots: list[SessionSnapshot] = []

    def write(self, snapshot: SessionSnapshot) -> None:
        self.snapshots.append(snapshot)

    def read(self, session_id: str) -> SessionSnapshot | None:
        return self.snapshots[-1] if self.snapshots else None


class _RecordingHandler(CommandHandler):
    def __init__(self, output: UserOutput, *, changed: bool) -> None:
        self._output = output
        self._changed = changed
        self.calls: list[SlashCommand] = []

    def execute(self, command: SlashCommand) -> bool:
        self.calls.append(command)
        self._output.print(f"/{command.command} handled")
        return self._changed


def _runtime() -> tuple[Repl, _MemoryEventStore, SessionService]:
    console = Console(record=True)
    output = RichOutput(console)
    events = _MemoryEventStore()
    states = _MemoryStateStore()
    session = SessionService(
        events,
        states,
        workspace_root="/repo",
        clock=lambda: "2026-06-27T10:00:00+08:00",
        id_factory=lambda: "ses_test",
    )
    session.start()

    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            "plan",
            IntentKind.MODE_CHANGE,
            CommandCategory.WRITE,
            "切换到计划模式",
            mode=SessionMode.PLAN,
        )
    )
    registry.register(
        CommandSpec(
            "config",
            IntentKind.SLASH_COMMAND,
            CommandCategory.WRITE,
            "查看 / 修改配置",
            handler=_RecordingHandler(output, changed=True),
        )
    )
    registry.register(
        CommandSpec(
            "status",
            IntentKind.SLASH_COMMAND,
            CommandCategory.READ,
            "查看状态",
            handler=_RecordingHandler(output, changed=False),
        )
    )
    repl = Repl(
        console=console,
        router=IntentRouter(registry),
        registry=registry,
        state=SessionState(),
        output=output,
        session=session,
    )
    return repl, events, session


def test_repl_records_user_message_and_mode_change(monkeypatch) -> None:
    repl, events, session = _runtime()
    monkeypatch.setattr("forgecli.interfaces.cli.repl.time.sleep", lambda _: None)

    repl._process_line("hello")
    repl._process_line("/plan")

    assert [event.type.value for event in events.events] == [
        "session_created",
        "user_message",
        "mode_changed",
    ]
    assert events.events[1].payload == {"text": "hello"}
    assert events.events[2].payload == {"mode": "plan"}
    assert session.current().mode is SessionMode.PLAN


def test_repl_does_not_record_slash_commands_yet() -> None:
    repl, events, _session = _runtime()

    repl._process_line("/status")
    repl._process_line("/config set output.theme light")

    assert events.events == []
