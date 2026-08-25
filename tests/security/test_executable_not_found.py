"""跑不了的命令直接拒, 不占用人类打断 (ADR-0013 §5 的补充).

起因是一个真实现象: Windows 上模型不知道自己在哪个平台, 发 `ls -al`. 受控 PATH 里找不到
`ls`, 原来的处理是 ASK —— **Forge 明知这条命令跑不了, 还是弹 HITL 问用户批不批准**, 而
批准也没用: 执行用的是同一份受控 PATH, 点了同意照样失败.

改成 DENY 之后它变成一条 `policy_denied` observation, disposition 是 BLOCKED, 模型下一轮
自己换命令.

这里最要紧的**不是**那条 DENY, 而是它别误伤内建命令: `export` `set` `dir` 本来就不在
PATH 上, 判死它们会让最普通的操作彻底不可用.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from forgecli.application.security.analyzers.shell_analyzer import (
    ShellCapabilityAnalyzer,
)
from forgecli.application.security.executable_resolver import ExecutableResolver
from forgecli.application.security.policy_engine import PolicyEngine
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.intents import SessionMode
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.decision import AuthorizationDecision
from forgecli.domain.security.findings import AnalysisFindings
from forgecli.domain.security.shell.builtins import (
    dialect_has_closed_builtin_set,
    is_builtin,
)
from forgecli.domain.security.shell.command_plan import ShellKind
from forgecli.domain.security.vocabulary import Decision, DecisionReason
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
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from support.fakes import PROFILE


def _context(workspace: Path, shell_kind: str) -> ExecutionContext:
    return ExecutionContext(
        cwd=str(workspace),
        workspace_roots=(str(workspace),),
        # 受控 PATH 故意只有系统目录: 与产品里一致, 也保证 `ls` 存在而胡编的命令不存在.
        environment={"PATH": "/usr/bin:/bin", "HOME": str(workspace)},
        filesystem=OsFileSystemView(),
        profile=replace(
            PROFILE,
            shell_launch=replace(PROFILE.shell_launch, kind=shell_kind),
        ),
    )


def _plan(command: str, cwd: str, shell_kind: str) -> ToolPlan:
    return ToolPlan(
        plan_id="inv-1",
        tool_name="shell_run",
        spec_hash="spec",
        normalized_input=MappingProxyType({"command": command}),
        capabilities=frozenset({Capability.EXECUTE_SHELL}),
        effects=PlanEffects(),
        target_resolution=TargetResolution.UNKNOWN,
        workspace_scope=WorkspaceScope.IN_WORKSPACE,
        execution_context=ExecutionContextRef(
            cwd=cwd,
            environment_hash="env",
            filesystem_view_version="v1",
            toolchain_id="default",
        ),
        declaration_confidence=DeclarationConfidence.DECLARED,
        # cwd 要指向真实目录, 否则 glob 展开无处落脚.
        analysis_subject=ShellSubject(
            raw_command=command,
        ),
    )


def _analyze(
    command: str, workspace: Path, shell_kind: str = "posix"
) -> AnalysisFindings:
    analyzer = ShellCapabilityAnalyzer(ExecutableResolver())
    plan = _plan(command, str(workspace), shell_kind)
    return analyzer.analyze(
        AnalysisFindings(plan=plan),
        _policy(),
        _context(workspace, shell_kind),
    )


def _policy() -> PolicyContext:
    return PolicyContext(
        mode=SessionMode.ACCEPT_EDITS,
        session_id="s1",
        turn_id="t1",
        execution_profile_hash=PROFILE.execution_profile_hash,
        workspace_id="ws-1",
    )


def _decide(
    command: str, workspace: Path, shell_kind: str = "posix"
) -> AuthorizationDecision:
    return PolicyEngine().decide(_analyze(command, workspace, shell_kind), _policy())


# ---- 内建命令不能被误伤 ----


@pytest.mark.parametrize("token", ["export", "set", "cd", "unset", "alias", "source"])
def test_posix_builtins_are_recognised(token: str) -> None:
    assert is_builtin(token, ShellKind.POSIX)


@pytest.mark.parametrize("token", ["dir", "set", "echo", "cls", "DIR", "Set"])
def test_cmd_builtins_are_case_insensitive(token: str) -> None:
    """cmd 不区分大小写, POSIX 区分 —— 两边不能用同一套比较."""
    assert is_builtin(token, ShellKind.CMD)


def test_posix_builtins_are_case_sensitive() -> None:
    assert not is_builtin("Export", ShellKind.POSIX)


@pytest.mark.parametrize("token", ["./cd", "/usr/bin/cd", r"C:\x\dir"])
def test_a_path_is_never_a_builtin(token: str) -> None:
    """带路径分隔符的指的是文件, 不是内建 —— `./cd` 是工作区里一个可执行文件."""
    assert not is_builtin(token, ShellKind.POSIX)
    assert not is_builtin(token, ShellKind.CMD)


def test_powershell_has_no_closed_builtin_set() -> None:
    """cmdlet 数以千计且可动态注册, 枚举不出来. 判不出来就别判死."""
    assert not dialect_has_closed_builtin_set(ShellKind.POWERSHELL)
    assert not is_builtin("Get-ChildItem", ShellKind.POWERSHELL)


@pytest.mark.parametrize("command", ["export FOO=1", "cd /tmp", "set -e", "unset FOO"])
def test_a_builtin_only_command_is_not_reported_unrunnable(
    command: str, tmp_path: Path
) -> None:
    """改动前这些会被判成"受控 PATH 中找不到", 改成 DENY 之后就彻底不可用了."""
    findings = _analyze(command, tmp_path)
    assert findings.unrunnable is None
    assert not any(
        fact.code in ("executable_not_found", "executable_unresolved")
        for fact in findings.risk_facts
    )


# ---- 真的跑不了的才拒 ----


def test_an_unknown_command_is_denied_not_asked(tmp_path: Path) -> None:
    """这就是 Windows 上 `ls -al` 的处境: 既不是内建, 也不在受控 PATH 上."""
    decision = _decide("definitely-not-a-real-command-xyz --flag", tmp_path)

    assert decision.decision is Decision.DENY
    assert decision.reason is DecisionReason.EXECUTABLE_NOT_FOUND


def test_the_denial_says_which_platform_and_dialect(tmp_path: Path) -> None:
    """光说"找不到"模型不知道往哪个方向改. 它多半是照着另一个平台的习惯发的命令."""
    decision = _decide("definitely-not-a-real-command-xyz", tmp_path)
    detail = " ".join(fact.detail for fact in decision.risk_facts)

    assert "definitely-not-a-real-command-xyz" in detail
    assert PROFILE.platform in detail
    assert "posix" in detail


def test_a_real_command_still_resolves(tmp_path: Path) -> None:
    """别把这条规则做成"只要在受控 PATH 外就拒" —— /bin/ls 是能找到的."""
    findings = _analyze("ls -la", tmp_path)
    assert findings.unrunnable is None


def test_one_bad_unit_in_a_pipeline_is_enough(tmp_path: Path) -> None:
    """管道里有一个跑不了, 整条就跑不了."""
    findings = _analyze("ls | definitely-not-a-real-command-xyz", tmp_path)
    assert findings.unrunnable is DecisionReason.EXECUTABLE_NOT_FOUND


# ---- PowerShell 保守处理 ----


def test_an_unknown_powershell_command_stays_ask(tmp_path: Path) -> None:
    """cmdlet 枚举不出来, 所以"不在 PATH 上"完全可能是个正常的 cmdlet."""
    findings = _analyze("Get-ChildItem -Force", tmp_path, shell_kind="powershell")

    assert findings.unrunnable is None
    assert findings.requires_ask is not None


# ---- 与 Hard Deny 的先后 ----


def test_hard_deny_still_wins(tmp_path: Path) -> None:
    """一条既危险又跑不了的命令, 审计里该记的是安全理由, 不是"找不到命令"."""
    decision = _decide("sudo definitely-not-a-real-command-xyz", tmp_path)

    assert decision.decision is Decision.DENY
    assert decision.reason is DecisionReason.HARD_DENY_PRIVILEGE_ESCALATION


def test_unrunnable_outranks_mode_budget(tmp_path: Path) -> None:
    """跑不了的东西不该再走模式预算与人类审批.

    改动前正是这里出的问题: EXECUTE_SHELL 在 accept_edits 下超预算 -> ASK, 于是用户被
    问了一个"批准了也没用"的问题.
    """
    decision = _decide("definitely-not-a-real-command-xyz", tmp_path)
    assert decision.decision is not Decision.ASK
