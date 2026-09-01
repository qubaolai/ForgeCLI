"""渐进式授权: always 沉淀成一条受限 Allow 规则 (ADR-0013 §5.1).

三条边界最要紧, 每条都对应一种"看起来方便但实际是后门"的做法:
只有 shell_run 能学; 命中规则不跳过 Hard Deny; 规则不泛化.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from pytest import mark

from forgecli.application.security.learned_rules import LearnedRuleService
from forgecli.domain.intents import SessionMode
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
from forgecli.infrastructure.security.json_learned_rules_store import (
    JsonLearnedRuleStore,
)

IDENTITY = "sha256:abc123"


def _plan(command: str = "poetry run pytest") -> ToolPlan:
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


def _decision(
    *,
    command: str = "poetry run pytest",
    identity: str = IDENTITY,
    decision: Decision = Decision.ASK,
    reason: DecisionReason = DecisionReason.MODE_REQUIRES_APPROVAL,
    mandatory: bool = False,
    names: tuple[str, ...] = ("poetry",),
) -> AuthorizationDecision:
    return AuthorizationDecision(
        decision=decision,
        reason=reason,
        findings=AnalysisFindings(
            plan=_plan(command),
            executable_identity_hash=identity,
            executable_names=names,
        ),
        mandatory=mandatory,
    )


def _policy(**overrides: object) -> PolicyContext:
    base: dict[str, object] = {
        "mode": SessionMode.ACCEPT_EDITS,
        "session_id": "s1",
        "turn_id": "t1",
        "execution_profile_hash": "profile",
        "workspace_id": "ws-1",
    }
    base.update(overrides)
    return PolicyContext(**base)  # type: ignore[arg-type]


# ---- 什么能学 ----


def test_a_shell_ask_can_be_learned() -> None:
    assert LearnedRuleService().learnable(_decision()) is True


def test_a_tool_without_an_executable_identity_cannot_be_learned() -> None:
    """只有 shell 分析器解析得出身份. 其余工具的规则绑不住, 给了也命不中."""
    assert LearnedRuleService().learnable(_decision(identity="")) is False


def test_mandatory_ask_cannot_be_learned() -> None:
    decision = _decision(
        mandatory=True, reason=DecisionReason.EXTERNAL_IRREVERSIBLE_EFFECT
    )
    assert LearnedRuleService().learnable(decision) is False


def test_a_denied_request_cannot_be_learned() -> None:
    decision = _decision(
        decision=Decision.DENY, reason=DecisionReason.HARD_DENY_DESTRUCTIVE
    )
    assert LearnedRuleService().learnable(decision) is False


@mark.parametrize(
    "reason",
    [
        DecisionReason.SCRIPT_EXECUTION,
        DecisionReason.PARSE_INCOMPLETE,
    ],
)
def test_an_unfinished_analysis_cannot_be_learned(reason: DecisionReason) -> None:
    """ "分析没做完"不能沉淀成规则.

    否则一次退让会变成永久放行: 一个正文看不透的脚本被点一次 always 之后就永久允许,
    而规则绑的是命令行不是脚本内容, 脚本随后改成什么都照样命中.
    """
    assert LearnedRuleService().learnable(_decision(reason=reason)) is False


# ---- 命中 ----


def test_the_same_command_hits_the_rule_next_time() -> None:
    service = LearnedRuleService()
    service.record(_decision(), _policy(), ApprovalScope.WORKSPACE)
    assert service.find(_decision(), _policy()) is not None


def test_a_different_command_does_not_hit() -> None:
    service = LearnedRuleService()
    service.record(_decision(), _policy(), ApprovalScope.WORKSPACE)
    assert service.find(_decision(command="rm -rf build"), _policy()) is None


def test_a_different_binary_does_not_hit() -> None:
    """同一条命令字符串, 换了背后的可执行文件, 规则必须失效."""
    service = LearnedRuleService()
    service.record(_decision(), _policy(), ApprovalScope.WORKSPACE)
    assert service.find(_decision(identity="sha256:other"), _policy()) is None


def test_another_workspace_does_not_hit() -> None:
    service = LearnedRuleService()
    service.record(_decision(), _policy(), ApprovalScope.WORKSPACE)
    assert service.find(_decision(), _policy(workspace_id="ws-2")) is None


def test_another_mode_does_not_hit() -> None:
    service = LearnedRuleService()
    service.record(_decision(), _policy(), ApprovalScope.WORKSPACE)
    assert service.find(_decision(), _policy(mode=SessionMode.PLAN)) is None


def test_a_changed_execution_profile_does_not_hit() -> None:
    service = LearnedRuleService()
    service.record(_decision(), _policy(), ApprovalScope.WORKSPACE)
    assert service.find(_decision(), _policy(execution_profile_hash="other")) is None


def test_a_new_session_still_hits_a_workspace_rule() -> None:
    """workspace 范围的意义就是跨会话有效 —— 否则它与 session 范围没有区别."""
    service = LearnedRuleService()
    service.record(_decision(), _policy(session_id="s1"), ApprovalScope.WORKSPACE)
    assert service.find(_decision(), _policy(session_id="s2")) is not None


# ---- 记录与撤销 ----


def test_once_never_creates_a_rule() -> None:
    service = LearnedRuleService()
    assert service.record(_decision(), _policy(), ApprovalScope.ONCE) is None
    assert service.rules == ()


def test_recording_an_unlearnable_request_is_a_no_op_not_an_error() -> None:
    """用户选了 always 但这次恰好不可学: 本次仍按 once 放行, 只是学不到东西."""
    service = LearnedRuleService()
    recorded = service.record(
        _decision(identity=""), _policy(), ApprovalScope.WORKSPACE
    )
    assert recorded is None


def test_a_revoked_rule_stops_matching() -> None:
    service = LearnedRuleService()
    rule = service.record(_decision(), _policy(), ApprovalScope.WORKSPACE)
    assert rule is not None
    assert service.revoke(rule.rule_id) is True
    assert service.find(_decision(), _policy()) is None


# ---- 落盘 ----


def test_rules_survive_a_restart(tmp_path: Path) -> None:
    store = JsonLearnedRuleStore(tmp_path / "rules.json")
    LearnedRuleService(store, workspace_id="ws-1").record(
        _decision(), _policy(), ApprovalScope.WORKSPACE
    )
    revived = LearnedRuleService(store, workspace_id="ws-1")
    assert revived.find(_decision(), _policy()) is not None


def test_arguments_are_never_persisted(tmp_path: Path) -> None:
    """参数一律不落盘: 命令行里可能带路径, 主机名甚至凭证片段.

    落盘的只有匹配用的哈希, 加上一个可执行文件基名 —— `curl` 这三个字母带不出任何
    东西, 而它后面跟的 header 带得出.
    """
    path = tmp_path / "rules.json"
    service = LearnedRuleService(JsonLearnedRuleStore(path), workspace_id="ws-1")
    service.record(
        _decision(
            command="curl -H 'Authorization: Bearer sk-secret' https://api.internal",
            names=("curl",),
        ),
        _policy(),
        ApprovalScope.WORKSPACE,
    )
    saved = path.read_text("utf-8")
    assert "sk-secret" not in saved
    assert "Authorization" not in saved
    assert "api.internal" not in saved
    assert "-H" not in saved


def test_the_label_round_trips_so_rules_can_be_listed(tmp_path: Path) -> None:
    """没有可读标签, /rules 就只是一串 rule_id, 用户无从判断该撤销哪一条."""
    path = tmp_path / "rules.json"
    LearnedRuleService(JsonLearnedRuleStore(path), workspace_id="ws-1").record(
        _decision(names=("poetry", "pytest")), _policy(), ApprovalScope.WORKSPACE
    )
    revived = LearnedRuleService(JsonLearnedRuleStore(path), workspace_id="ws-1")
    assert revived.rules[0].label == "poetry | pytest"


def test_the_label_does_not_affect_matching() -> None:
    """标签只给人看. 换个标签不该变成另一条规则, 也不该让旧规则失配."""
    service = LearnedRuleService()
    rule = service.record(
        _decision(names=("poetry",)), _policy(), ApprovalScope.WORKSPACE
    )
    assert rule is not None
    assert service.find(_decision(names=("something-else",)), _policy()) is not None


def test_a_corrupt_entry_is_skipped_not_fatal(tmp_path: Path) -> None:
    """一条坏规则不该让会话起不来; 少一条 Allow 的后果是多问一次, 方向安全."""
    path = tmp_path / "rules.json"
    path.write_text('{"rules": [{"rule_id": "x"}]}\n', encoding="utf-8")
    assert JsonLearnedRuleStore(path).load() == ()
