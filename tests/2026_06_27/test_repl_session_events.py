"""REPL 分派写入会话事件。

29 日后，自然语言 turn 由 AgentTurnService 处理；模式切换也归一为 SlashCommand
handler，不再通过独立 ModeChange intent 或 REPL 内存态。
"""

from __future__ import annotations

from rich.console import Console

from forgecli.application.agent_turn.agent_turn_service import AgentTurnService
from forgecli.application.intent_router import IntentRouter
from forgecli.application.interaction_ports import UserOutput
from forgecli.application.session import (
    EventStore,
    SessionEvent,
    SessionService,
    SessionSnapshot,
    StateStore,
)
from forgecli.application.slash_commands import CommandHandler, CommandRegistry
from forgecli.application.slash_commands.registry import CommandSpec
from forgecli.domain.intents import SessionMode, SlashCommand
from forgecli.interfaces.cli.commands.mode_command import ModeCommand
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
    def __init__(self, output: UserOutput) -> None:
        self._output = output
        self.calls: list[SlashCommand] = []

    def execute(self, command: SlashCommand) -> bool:
        self.calls.append(command)
        self._output.print(f"/{command.command} handled")
        return True


def _runtime() -> tuple[Repl, _MemoryEventStore, SessionService]:
    console = Console(record=True)
    output = RichOutput(console)
    events = _MemoryEventStore()
    states = _MemoryStateStore()
    session = SessionService(
        events,
        states,
        workspace_root="/repo",
        clock=lambda: "2026-06-29T10:00:00+08:00",
        id_factory=lambda: "ses_test",
    )
    session.start()

    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            "plan",
            "切换到计划模式",
            handler=ModeCommand(SessionMode.PLAN, session, output),
        )
    )
    registry.register(
        CommandSpec(
            "config",
            "查看 / 修改配置",
            handler=_RecordingHandler(output),
        )
    )
    registry.register(
        CommandSpec(
            "status",
            "查看状态",
            handler=_RecordingHandler(output),
        )
    )
    repl = Repl(
        console=console,
        router=IntentRouter(registry),
        registry=registry,
        output=output,
        session=session,
        agent_turn=AgentTurnService(session),
    )
    return repl, events, session


def test_repl_records_user_assistant_pair_and_mode_command() -> None:
    repl, events, session = _runtime()

    repl._process_line("hello")
    repl._process_line("/plan")

    assert [event.type.value for event in events.events] == [
        "session_created",
        "user_message",
        "assistant_message",
        "mode_changed",
    ]
    assert events.events[1].payload == {
        "turn_id": "turn_0001",
        "role": "user",
        "text": "hello",
    }
    assert events.events[2].payload["turn_id"] == "turn_0001"
    assert events.events[2].payload["role"] == "assistant"
    assert events.events[2].payload["status"] == "completed"
    assert events.events[3].payload == {"mode": "plan"}
    assert session.current().mode is SessionMode.PLAN


def test_repl_does_not_record_generic_slash_command_events_yet() -> None:
    repl, events, _session = _runtime()

    repl._process_line("/status")
    repl._process_line("/config set output.theme light")

    assert events.events == []
