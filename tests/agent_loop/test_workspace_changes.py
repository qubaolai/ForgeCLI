"""agent loop 对工作区变化的感知与 LLM 通知。"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.agent.actions import LoopObservation, ToolRequestAction
from forgecli.domain.agent.run_events import AgentRunEventKind
from forgecli.domain.workspace.changes import (
    WorkspaceChangeSource,
    WorkspaceFileState,
    WorkspaceSnapshot,
)
from support.loop_harness import ScriptedGateway, call, loop_input, loop_with, response


@dataclass
class _Snapshots:
    values: list[WorkspaceSnapshot]

    def snapshot(self) -> WorkspaceSnapshot:
        if len(self.values) > 1:
            return self.values.pop(0)
        return self.values[0]


def _file(path: str, *, mtime_ns: int) -> WorkspaceFileState:
    return WorkspaceFileState(
        path=path,
        size=mtime_ns,
        mtime_ns=mtime_ns,
        file_identity=f"inode-{mtime_ns}",
    )


def _empty() -> WorkspaceSnapshot:
    return WorkspaceSnapshot()


def test_agent_tool_changes_are_reported_to_the_next_model_call() -> None:
    changed = WorkspaceSnapshot(files=(_file("/ws/a.py", mtime_ns=1),))
    snapshots = _Snapshots([_empty(), _empty(), _empty(), _empty(), changed, changed])
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(call("fs_read", "c1"),)),
            response("完成"),
        ]
    )
    loop, events = loop_with(gateway, workspace_snapshot_provider=snapshots)

    loop.start(loop_input())
    loop.observe(LoopObservation(content="ok"))

    notice = gateway.requests[1].messages[-1].content[0].text  # type: ignore[union-attr]
    assert "本 agent" in notice
    assert "/ws/a.py" in notice
    changed_events = events.of(AgentRunEventKind.WORKSPACE_CHANGED)
    assert changed_events[0].payload.changes[0].source is WorkspaceChangeSource.AGENT


def test_external_changes_are_reported_before_the_next_model_call() -> None:
    changed = WorkspaceSnapshot(files=(_file("/ws/a.py", mtime_ns=2),))
    # start, before first model, before tool, after tool, before second model
    snapshots = _Snapshots([_empty(), _empty(), _empty(), _empty(), _empty(), changed])
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(call("fs_read", "c1"),)),
            response("完成"),
        ]
    )
    loop, events = loop_with(gateway, workspace_snapshot_provider=snapshots)

    loop.start(loop_input())
    loop.observe(LoopObservation(content="ok"))

    notice = gateway.requests[1].messages[-1].content[0].text  # type: ignore[union-attr]
    assert "外部参与者" in notice
    assert "其他 agent 或人类" in notice
    changed_events = events.of(AgentRunEventKind.WORKSPACE_CHANGED)
    assert changed_events[0].payload.changes[0].source is WorkspaceChangeSource.EXTERNAL


def test_snapshot_diff_reports_create_modify_and_delete() -> None:
    from forgecli.application.workspace.monitor import diff_workspace_snapshots

    before = WorkspaceSnapshot(
        files=(
            _file("/ws/deleted.py", mtime_ns=1),
            _file("/ws/changed.py", mtime_ns=1),
        )
    )
    after = WorkspaceSnapshot(
        files=(
            _file("/ws/changed.py", mtime_ns=2),
            _file("/ws/created.py", mtime_ns=1),
        )
    )

    changes = diff_workspace_snapshots(
        before, after, source=WorkspaceChangeSource.EXTERNAL
    )

    assert [(item.path, item.kind.value) for item in changes] == [
        ("/ws/changed.py", "modified"),
        ("/ws/created.py", "created"),
        ("/ws/deleted.py", "deleted"),
    ]


def test_tool_dispatch_does_not_stop_at_a_total_call_counter() -> None:
    gateway = ScriptedGateway(
        responses=[
            response(
                tool_calls=(
                    call("fs_read", "c1"),
                    call("fs_read", "c2"),
                )
            )
        ]
    )
    loop, _ = loop_with(gateway)

    first = loop.start(loop_input())
    assert isinstance(first, ToolRequestAction)
    loop._tool_calls = 9999  # type: ignore[attr-defined]

    second = loop.observe(LoopObservation(content="ok"))

    assert isinstance(second, ToolRequestAction)
    assert second.request.tool_call_id == "c2"
