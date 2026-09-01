"""target_declaration_ability 是 target_resolution 的上界 (ADR-0004 §4).

声明 STATIC 等于承诺"我每次都能从入参算出确定的目标集合". 兑现不了却放行的话, 下游会按
"目标已封闭"走快速裁决 —— 一个实际上没封闭的调用因此拿到普通 ALLOW. 这条上界原来只写在
docstring 里, 没有任何地方校验.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest

from forgecli.application.security.authorization_service import ToolAuthorizationService
from forgecli.application.security.policy_engine import PolicyEngine
from forgecli.application.security.wiring import build_analyzer_registry
from forgecli.application.tool_request.coordinator import ToolRequestCoordinator
from forgecli.application.tool_request.observations import ObservationKind
from forgecli.application.tools.registry import ToolRegistry
from forgecli.application.tools.runtime import ToolRuntime
from forgecli.application.tools.tool import Tool, ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.agent.actions import ToolRequest
from forgecli.domain.intents import SessionMode
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.protected_paths import ProtectedPathPolicy
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.errors import PreparationError
from forgecli.domain.tool.plan import (
    DeclarationConfidence,
    PlanEffects,
    TargetResolution,
    ToolPlan,
)
from forgecli.domain.tool.result import ContentPart, ToolResult, ToolResultStatus
from forgecli.domain.tool.spec import (
    TargetDeclarationAbility,
    ToolAction,
    ToolSpec,
)
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from forgecli.shared.cancellation import CancelToken
from support.fakes import PROFILE, SilentToolRunObserver

# ---- 上界判定本身 ----


@pytest.mark.parametrize(
    ("ability", "resolution", "allowed"),
    [
        (TargetDeclarationAbility.STATIC, TargetResolution.STATIC, True),
        (TargetDeclarationAbility.STATIC, TargetResolution.FORGE_EXPANDED, False),
        (TargetDeclarationAbility.STATIC, TargetResolution.UNKNOWN, False),
        (TargetDeclarationAbility.EXPANDABLE, TargetResolution.STATIC, True),
        (TargetDeclarationAbility.EXPANDABLE, TargetResolution.FORGE_EXPANDED, True),
        (TargetDeclarationAbility.EXPANDABLE, TargetResolution.DYNAMIC, False),
        (TargetDeclarationAbility.OPAQUE, TargetResolution.UNKNOWN, True),
        (TargetDeclarationAbility.OPAQUE, TargetResolution.STATIC, True),
    ],
)
def test_permits(
    ability: TargetDeclarationAbility,
    resolution: TargetResolution,
    allowed: bool,
) -> None:
    assert ability.permits(resolution) is allowed


def test_declaring_less_and_delivering_more_is_fine() -> None:
    """比声明更强总是允许的: fs_delete 声明 EXPANDABLE, 删单个文件时给 STATIC."""
    assert TargetDeclarationAbility.EXPANDABLE.permits(TargetResolution.STATIC)


# ---- 链路上的强制 ----


def _spec(ability: TargetDeclarationAbility) -> ToolSpec:
    return ToolSpec(
        name="fake_tool",
        version="1",
        title="替身",
        description="替身",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        declared_capabilities=frozenset({Capability.WORKSPACE_READ}),
        target_declaration_ability=ability,
        default_timeout_seconds=5.0,
        action=ToolAction.READ,
    )


class LyingTool(Tool):
    """声明与产出不一致的工具. 真实世界里这是实现 bug, 这里用来钉住它会被挡下."""

    def __init__(
        self, ability: TargetDeclarationAbility, resolution: TargetResolution
    ) -> None:
        self._spec = _spec(ability)
        self._resolution = resolution

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def prepare(
        self, request: ToolInvocationRequest, context: ExecutionContext
    ) -> ToolPlan | PreparationError:
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name="fake_tool",
            spec_hash=self._spec.spec_hash,
            normalized_input=MappingProxyType({}),
            capabilities=frozenset({Capability.WORKSPACE_READ}),
            effects=PlanEffects(read_paths=(context.primary_root,)),
            target_resolution=self._resolution,
            workspace_scope=context.scope_of(context.primary_root),
            execution_context=context.to_ref(),
            declaration_confidence=DeclarationConfidence.DECLARED,
        )

    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name="fake_tool",
            status=ToolResultStatus.OK,
            content_parts=(ContentPart(text="执行了"),),
        )


def _dispatch(tmp_path: Path, tool: LyingTool):  # type: ignore[no-untyped-def]
    workspace = tmp_path / "ws"
    workspace.mkdir()
    registry = ToolRegistry()
    registry.register_all((tool,))
    coordinator = ToolRequestCoordinator(
        registry,
        ToolRuntime(registry),
        ToolAuthorizationService(
            build_analyzer_registry(
                ProtectedPathPolicy(roots=()),
            ),
            PolicyEngine(),
        ),
        observer=SilentToolRunObserver(),
    )
    context = ExecutionContext(
        cwd=str(workspace),
        workspace_roots=(str(workspace),),
        environment={"PATH": "/usr/bin:/bin"},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )
    return coordinator.handle(
        ToolRequest(name="fake_tool"),
        context=context,
        policy=PolicyContext(
            mode=SessionMode.ACCEPT_EDITS,
            session_id="s",
            turn_id="t",
            execution_profile_hash=PROFILE.execution_profile_hash,
        ),
    )


def test_a_tool_that_breaks_its_declaration_is_stopped(tmp_path: Path) -> None:
    observation = _dispatch(
        tmp_path,
        LyingTool(TargetDeclarationAbility.STATIC, TargetResolution.UNKNOWN),
    )
    assert observation.kind is ObservationKind.PREPARATION_FAILED
    assert observation.reason_code == "target_declaration_violation"
    assert "static" in observation.message
    assert "unknown" in observation.message


def test_the_violation_is_not_retryable(tmp_path: Path) -> None:
    """同一份代码会给出同样的结果, 让模型重试只是浪费一轮."""
    observation = _dispatch(
        tmp_path,
        LyingTool(TargetDeclarationAbility.STATIC, TargetResolution.DYNAMIC),
    )
    assert observation.can_retry is False


def test_a_tool_that_honours_its_declaration_runs(tmp_path: Path) -> None:
    observation = _dispatch(
        tmp_path,
        LyingTool(TargetDeclarationAbility.EXPANDABLE, TargetResolution.STATIC),
    )
    assert observation.kind is ObservationKind.TOOL_RESULT
