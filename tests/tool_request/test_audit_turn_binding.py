"""工具审计事件必须归属到发起它的那一轮 (ADR-0016 §9, 阶段 0).

审计 sink 是跨轮复用的长生命周期对象, turn_id 存在它自己身上. 之前 `bind_turn` 定义了
却没有任何调用者, 于是所有落盘的工具审计事件都带着构造时的空串 —— 事后没法回答
"哪一轮跑了这条命令", resume 时也对不上是哪次对话触发的写入.

这里钉住的是绑定时机: 每次 handle 入口都要认领当前轮次, 而不是装配时认领一次.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from forgecli.application.security.authorization_service import ToolAuthorizationService
from forgecli.application.security.policy_engine import PolicyEngine
from forgecli.application.security.wiring import build_analyzer_registry
from forgecli.application.tool_request.audit import ToolAuditSink
from forgecli.application.tool_request.coordinator import ToolRequestCoordinator
from forgecli.application.tool_request.observations import ObservationKind
from forgecli.application.tools.artifact_store import NullArtifactStore
from forgecli.application.tools.builtin import ReadFileTool
from forgecli.application.tools.registry import ToolRegistry
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.runtime import ToolRuntime
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.agent.actions import ToolRequest
from forgecli.domain.intents import SessionMode
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.decision import AuthorizationDecision
from forgecli.domain.security.protected_paths import ProtectedPathPolicy
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.tool.result import ToolResult
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from support.fakes import PROFILE, SilentToolRunObserver, unavailable_classifier


class RecordingAudit(ToolAuditSink):
    """记下每条审计事件当时绑定的 turn_id."""

    def __init__(self) -> None:
        self.turn_id = ""
        self.events: list[tuple[str, str]] = []  # (事件名, 当时的 turn_id)

    def bind_turn(self, turn_id: str) -> None:
        self.turn_id = turn_id

    def tool_requested(
        self, plan: ToolPlan, *, invocation_id: str, authorization_id: str
    ) -> None:
        self.events.append(("tool_requested", self.turn_id))

    def tool_completed(self, result: ToolResult, *, plan_hash: str) -> None:
        self.events.append(("tool_completed", self.turn_id))

    def tool_rejected(
        self, tool_name: str, *, invocation_id: str, reason_code: str, message: str
    ) -> None:
        self.events.append(("tool_rejected", self.turn_id))

    def policy_decision(
        self, decision: AuthorizationDecision, *, invocation_id: str
    ) -> None:
        self.events.append(("policy_decision", self.turn_id))

    def approval_event(self, name: str, payload: Mapping[str, object]) -> None:
        self.events.append((name, self.turn_id))

    def recovery_event(self, name: str, payload: Mapping[str, object]) -> None:
        self.events.append((name, self.turn_id))


def _build(
    tmp_path: Path,
) -> tuple[ToolRequestCoordinator, ExecutionContext, RecordingAudit]:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "main.py").write_text("print(1)\n", encoding="utf-8")

    registry = ToolRegistry()
    registry.register_all((ReadFileTool(ResourceGovernor(), NullArtifactStore()),))
    audit = RecordingAudit()
    coordinator = ToolRequestCoordinator(
        registry,
        ToolRuntime(registry),
        ToolAuthorizationService(
            build_analyzer_registry(
                ProtectedPathPolicy(roots=()),
                classifier=unavailable_classifier(),
            ),
            PolicyEngine(),
        ),
        audit=audit,
        observer=SilentToolRunObserver(),
        workspace_id="ws-audit",
    )
    context = ExecutionContext(
        cwd=str(workspace),
        workspace_roots=(str(workspace),),
        environment={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )
    return coordinator, context, audit


def _policy(turn_id: str) -> PolicyContext:
    return PolicyContext(
        mode=SessionMode.ACCEPT_EDITS,
        session_id="sess-1",
        turn_id=turn_id,
        execution_profile_hash=PROFILE.execution_profile_hash,
    )


def _read(coordinator: ToolRequestCoordinator, context: ExecutionContext, turn: str):  # type: ignore[no-untyped-def]
    return coordinator.handle(
        ToolRequest(name="fs.read_file", arguments={"path": "main.py"}),
        context=context,
        policy=_policy(turn),
    )


def test_audit_events_carry_the_current_turn_id(tmp_path: Path) -> None:
    coordinator, context, audit = _build(tmp_path)

    observation = _read(coordinator, context, "turn-1")

    assert observation.kind is ObservationKind.TOOL_RESULT
    assert audit.events, "至少要有 policy_decision 与 tool_requested"
    assert {turn for _, turn in audit.events} == {"turn-1"}


def test_binding_follows_the_turn_across_calls(tmp_path: Path) -> None:
    """同一个 sink 连续服务两轮: 第二轮的事件不能还挂在第一轮上."""
    coordinator, context, audit = _build(tmp_path)

    _read(coordinator, context, "turn-1")
    first_round = list(audit.events)
    _read(coordinator, context, "turn-2")
    second_round = audit.events[len(first_round) :]

    assert {turn for _, turn in first_round} == {"turn-1"}
    assert {turn for _, turn in second_round} == {"turn-2"}


def test_binding_happens_before_the_first_audit_write(tmp_path: Path) -> None:
    """policy_decision 是本链路最早的审计写入, 它就必须已经带上 turn_id."""
    coordinator, context, audit = _build(tmp_path)

    _read(coordinator, context, "turn-9")

    assert audit.events[0][0] == "policy_decision"
    assert audit.events[0][1] == "turn-9"
