"""/rules: 列出与撤销 always 学习规则 (ADR-0013 §5.1).

一个只进不出的授权面本身就是安全问题: 用户能创建规则却看不到自己批准过什么, 也撤不掉.
"""

from __future__ import annotations

import io
from types import MappingProxyType

from rich.console import Console

from forgecli.application.security.learned_rules import LearnedRuleService
from forgecli.domain.execution.profile import ExecutionProfile
from forgecli.domain.intents import SessionMode, SlashCommand
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.decision import AuthorizationDecision
from forgecli.domain.security.findings import AnalysisFindings
from forgecli.domain.security.vocabulary import ApprovalScope, Decision, DecisionReason
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.plan import (
    DeclarationConfidence,
    ExecutionContextRef,
    PlanEffects,
    ShellSubject,
    TargetResolution,
    ToolPlan,
    WorkspaceScope,
)
from forgecli.interfaces.cli.commands.rules_command import RulesCommand
from forgecli.interfaces.cli.output import RichOutput
from support.fakes import PROFILE


def _plan(command: str) -> ToolPlan:
    return ToolPlan(
        plan_id="inv-1",
        tool_name="shell_run",
        spec_hash="spec",
        normalized_input=MappingProxyType({"command": command}),
        capabilities=frozenset({Capability.EXECUTE_SHELL}),
        effects=PlanEffects(read_paths=("/ws",)),
        target_resolution=TargetResolution.STATIC,
        workspace_scope=WorkspaceScope.IN_WORKSPACE,
        execution_context=ExecutionContextRef(
            cwd="/ws",
            environment_hash="env",
            filesystem_view_version="v1",
            toolchain_id="default",
        ),
        declaration_confidence=DeclarationConfidence.DECLARED,
        analysis_subject=ShellSubject(raw_command=command),
    )


def _decision(command: str = "poetry run pytest") -> AuthorizationDecision:
    return AuthorizationDecision(
        decision=Decision.ASK,
        reason=DecisionReason.MODE_REQUIRES_APPROVAL,
        findings=AnalysisFindings(
            plan=_plan(command),
            executable_identity_hash="sha256:abc",
            executable_names=("poetry",),
        ),
    )


def _policy(workspace_id: str = "ws-1") -> PolicyContext:
    return PolicyContext(
        mode=SessionMode.ACCEPT_EDITS,
        session_id="s1",
        turn_id="t1",
        execution_profile_hash=PROFILE.execution_profile_hash,
        workspace_id=workspace_id,
    )


def _run(
    service: LearnedRuleService,
    args: tuple[str, ...] = (),
    *,
    profile: ExecutionProfile = PROFILE,
    workspace_id: str = "ws-1",
) -> str:
    console = Console(file=io.StringIO(), width=120, no_color=True)
    RulesCommand(service, profile, workspace_id, RichOutput(console)).execute(
        SlashCommand("/rules", "rules", args)
    )
    stream = console.file
    assert isinstance(stream, io.StringIO)
    return stream.getvalue()


def test_an_empty_list_says_how_rules_get_created() -> None:
    text = _run(LearnedRuleService())

    assert "always" in text


def test_a_rule_is_listed_with_a_readable_label() -> None:
    """没有标签, 列表就是一串 rule_id, 用户无从判断该撤销哪一条."""
    service = LearnedRuleService()
    service.record(_decision(), _policy(), ApprovalScope.WORKSPACE)

    text = _run(service)

    assert "poetry" in text
    assert "生效中" in text


def test_other_workspaces_rules_are_not_listed() -> None:
    """workspace 范围的规则不跨项目命中, 列表也不该跨项目显示."""
    service = LearnedRuleService()
    service.record(_decision(), _policy(workspace_id="ws-2"), ApprovalScope.WORKSPACE)

    text = _run(service, workspace_id="ws-1")

    assert "poetry" not in text


def test_a_stale_rule_says_why_it_stopped_working() -> None:
    """用户的体感是"我明明批准过". 不给原因, 这件事就没法解释."""
    service = LearnedRuleService()
    service.record(_decision(), _policy(), ApprovalScope.WORKSPACE)
    changed = ExecutionProfile(
        platform=PROFILE.platform,
        isolation_level=PROFILE.isolation_level,
        trusted_path=("/opt/bin",),  # 受控 PATH 变了 -> 执行画像变了
        shell_launch=PROFILE.shell_launch,
        environment_allowlist=PROFILE.environment_allowlist,
        protected_roots_hash=PROFILE.protected_roots_hash,
        executable_resolution_version=PROFILE.executable_resolution_version,
        path_separator=PROFILE.path_separator,
    )

    text = _run(service, profile=changed)

    assert "已失效" in text
    assert "执行环境已变" in text


def test_revoking_stops_the_rule_from_matching() -> None:
    service = LearnedRuleService()
    rule = service.record(_decision(), _policy(), ApprovalScope.WORKSPACE)
    assert rule is not None

    text = _run(service, ("--revoke", rule.rule_id))

    assert "已撤销" in text
    assert service.find(_decision(), _policy()) is None


def test_revoking_an_unknown_id_changes_nothing() -> None:
    service = LearnedRuleService()
    service.record(_decision(), _policy(), ApprovalScope.WORKSPACE)

    text = _run(service, ("--revoke", "rule_nope"))

    assert "找不到" in text
    assert service.find(_decision(), _policy()) is not None


def test_revoke_without_an_id_does_not_revoke_everything() -> None:
    """漏了参数就清空授权列表是最糟的默认值."""
    service = LearnedRuleService()
    service.record(_decision(), _policy(), ApprovalScope.WORKSPACE)

    text = _run(service, ("--revoke",))

    assert "用法" in text
    assert service.find(_decision(), _policy()) is not None


def test_prune_drops_revoked_entries() -> None:
    service = LearnedRuleService()
    rule = service.record(_decision(), _policy(), ApprovalScope.WORKSPACE)
    assert rule is not None
    service.revoke(rule.rule_id)

    text = _run(service, ("--prune",))

    assert "清理了 1 条" in text
    assert service.rules == ()


def test_prune_keeps_live_rules() -> None:
    service = LearnedRuleService()
    service.record(_decision(), _policy(), ApprovalScope.WORKSPACE)

    _run(service, ("--prune",))

    assert service.find(_decision(), _policy()) is not None
