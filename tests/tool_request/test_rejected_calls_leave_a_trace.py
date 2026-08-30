"""没有执行的工具调用也必须留下终态与审计.

回归来自一次真实故障: 模型请求了不存在的 `fs_write_file`, 协调器把"未注册"回填给模型
就直接返回了 —— 既没有运行事件也没有会话事件. 于是 Web 上那次调用永远停在"未完成",
`events.jsonl` 里也查不到它发生过, 而模型其实早就收到了结论并改用 shell 绕路.

**两边看到的不是同一件事**, 这比少一条事件更糟: 用户看到一个悬空的调用, 无从判断它
失败了还是还在跑.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.application.agent_run.tool_observer import EventBusToolRunObserver
from forgecli.application.security.authorization_service import ToolAuthorizationService
from forgecli.application.security.policy_engine import PolicyEngine
from forgecli.application.security.wiring import build_analyzer_registry
from forgecli.application.tool_request.audit import ToolAuditSink
from forgecli.application.tool_request.coordinator import ToolRequestCoordinator
from forgecli.application.tool_request.observations import ObservationKind
from forgecli.application.tools.builtin.fs_read import ReadFileTool
from forgecli.application.tools.registry import ToolRegistry
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.runtime import ToolRuntime
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.agent.actions import ToolRequest
from forgecli.domain.agent.run_events import AgentRunEvent, AgentRunEventKind
from forgecli.domain.intents import SessionMode
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.decision import AuthorizationDecision
from forgecli.domain.security.protected_paths import ProtectedPathPolicy
from forgecli.domain.session.events import EventType
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.tool.result import ToolResult
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from support.fakes import PROFILE, NullArtifactStore


class _Audit(ToolAuditSink):
    def __init__(self) -> None:
        self.rejected: list[tuple[str, str]] = []

    def bind_turn(self, turn_id: str) -> None:
        return None

    def tool_requested(
        self, plan: ToolPlan, *, invocation_id: str, authorization_id: str
    ) -> None:
        return None

    def tool_completed(self, result: ToolResult, *, plan_hash: str) -> None:
        return None

    def tool_rejected(
        self, tool_name: str, *, invocation_id: str, reason_code: str, message: str
    ) -> None:
        self.rejected.append((tool_name, reason_code))

    def policy_decision(
        self, decision: AuthorizationDecision, *, invocation_id: str
    ) -> None:
        return None

    def recovery_event(
        self, event_type: EventType, payload: Mapping[str, object]
    ) -> None:
        return None


class _Collector:
    def __init__(self) -> None:
        self.events: list[AgentRunEvent] = []

    def on_event(self, event: AgentRunEvent) -> None:
        self.events.append(event)


def _build(tmp_path: Path):  # type: ignore[no-untyped-def]
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "main.py").write_text("print(1)\n", encoding="utf-8")
    registry = ToolRegistry()
    registry.register_all((ReadFileTool(ResourceGovernor(), NullArtifactStore()),))
    bus = AgentRunEventBus()
    collector = _Collector()
    bus.subscribe(collector)  # type: ignore[arg-type]
    audit = _Audit()
    coordinator = ToolRequestCoordinator(
        registry,
        ToolRuntime(registry),
        ToolAuthorizationService(
            build_analyzer_registry(
                ProtectedPathPolicy(roots=()),
            ),
            PolicyEngine(),
        ),
        audit=audit,
        observer=EventBusToolRunObserver(bus),
        workspace_id="ws-1",
    )
    context = ExecutionContext(
        cwd=str(workspace),
        workspace_roots=(str(workspace),),
        environment={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )
    return coordinator, context, audit, collector


def _policy() -> PolicyContext:
    return PolicyContext(
        mode=SessionMode.ACCEPT_EDITS,
        session_id="sess-1",
        turn_id="turn_0001",
        execution_profile_hash=PROFILE.execution_profile_hash,
    )


def test_an_unregistered_tool_still_reaches_a_terminal_event(tmp_path: Path) -> None:
    coordinator, context, audit, collector = _build(tmp_path)

    observation = coordinator.handle(
        ToolRequest(name="fs_write_file", arguments={"path": "a.txt"}),
        context=context,
        policy=_policy(),
    )

    assert observation.kind is ObservationKind.TOOL_UNAVAILABLE
    completed = [
        event
        for event in collector.events
        if event.kind is AgentRunEventKind.TOOL_COMPLETED
    ]
    assert len(completed) == 1, "未注册的工具也要发终态, 否则展示层停在未完成"
    assert completed[0].payload.tool_name == "fs_write_file"  # type: ignore[attr-defined]
    assert completed[0].payload.status == "tool_unavailable"  # type: ignore[attr-defined]
    assert "未注册" in completed[0].payload.error_summary  # type: ignore[attr-defined]
    assert audit.rejected == [("fs_write_file", "tool_unavailable")]


def test_a_preparation_failure_also_leaves_a_terminal_event(tmp_path: Path) -> None:
    """入参不合法同样是"没执行就结束", 不能只回填模型而对展示层沉默."""
    coordinator, context, audit, collector = _build(tmp_path)

    observation = coordinator.handle(
        ToolRequest(name="fs_read", arguments={}),
        context=context,
        policy=_policy(),
    )

    assert observation.kind is ObservationKind.PREPARATION_FAILED
    assert any(
        event.kind is AgentRunEventKind.TOOL_COMPLETED for event in collector.events
    )
    assert audit.rejected and audit.rejected[0][0] == "fs_read"


def test_a_successful_call_reports_exactly_one_terminal_event(tmp_path: Path) -> None:
    """兜底不能变成重复: 真跑过的调用仍然只有 _execute 发的那一条终态."""
    coordinator, context, audit, collector = _build(tmp_path)

    observation = coordinator.handle(
        ToolRequest(name="fs_read", arguments={"path": "main.py"}),
        context=context,
        policy=_policy(),
    )

    assert observation.kind is ObservationKind.TOOL_RESULT
    completed = [
        event
        for event in collector.events
        if event.kind is AgentRunEventKind.TOOL_COMPLETED
    ]
    assert len(completed) == 1
    assert audit.rejected == []


def test_a_terminal_event_says_whether_the_call_ever_ran(tmp_path: Path) -> None:
    """ "没执行"与"执行了然后失败了"是两回事, 展示层要能区分."""
    coordinator, context, _, collector = _build(tmp_path)

    coordinator.handle(
        ToolRequest(name="fs_write_file", arguments={"path": "a.txt"}),
        context=context,
        policy=_policy(),
    )
    coordinator.handle(
        ToolRequest(name="fs_read", arguments={"path": "main.py"}),
        context=context,
        policy=_policy(),
    )

    completed = [
        event.payload
        for event in collector.events
        if event.kind is AgentRunEventKind.TOOL_COMPLETED
    ]
    assert [item.executed for item in completed] == [False, True]  # type: ignore[attr-defined]
    assert completed[0].error_code == "tool_unavailable"  # type: ignore[attr-defined]


def test_the_prepared_event_names_the_paths_it_will_touch(tmp_path: Path) -> None:
    """只报个数的话, "影响 1 个目标"这句话没法核对: 用户要看的是哪一个."""
    coordinator, context, _, collector = _build(tmp_path)

    coordinator.handle(
        ToolRequest(name="fs_read", arguments={"path": "main.py"}),
        context=context,
        policy=_policy(),
    )

    prepared = next(
        event.payload
        for event in collector.events
        if event.kind is AgentRunEventKind.TOOL_PREPARED
    )
    assert prepared.targets == (str((tmp_path / "ws" / "main.py").resolve()),)  # type: ignore[attr-defined]
    assert prepared.workspace_scope == "in_workspace"  # type: ignore[attr-defined]
    assert prepared.arguments == (("path", "main.py"),)  # type: ignore[attr-defined]


def test_a_finished_call_reports_structured_mechanics(tmp_path: Path) -> None:
    """从一句"1024 字节 · 退出码 1"里再解析出退出码, 是把展示格式当成了数据接口."""
    coordinator, context, _, collector = _build(tmp_path)

    coordinator.handle(
        ToolRequest(name="fs_read", arguments={"path": "main.py"}),
        context=context,
        policy=_policy(),
    )

    completed = next(
        event.payload
        for event in collector.events
        if event.kind is AgentRunEventKind.TOOL_COMPLETED
    )
    assert completed.bytes_out == len(b"print(1)\n")  # type: ignore[attr-defined]
    assert completed.error_code == ""  # type: ignore[attr-defined]
    assert completed.artifact_count == 0  # type: ignore[attr-defined]
