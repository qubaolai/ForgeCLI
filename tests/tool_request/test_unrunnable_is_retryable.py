"""跑不了 != 不许跑 (2026-09-04 修复).

一次实测: 模型发了一条三行命令, 中间一行是 `# 在 SecurityConfig 中添加权限配置`.
扫描器把 `#` 当成那一行的第一个词, 于是它成了这条命令的可执行文件, 受控 PATH 上自然
找不到 —— 整条命令被判 EXECUTABLE_NOT_FOUND -> DENY, 模型收到的是"这条命令在当前
环境下无法执行" (forge-20260904-133236 的 step 51). 那条命令在 bash 里跑得好好的.

两层各钉一条:

- 扫描器不该把注释当命令.
- 就算下一个解析缺口再让 `unrunnable` 置位, 回填也不能说成安全拒绝 —— 那会让模型
  以为换个写法是绕过, 而这里换个写法恰恰是对的.
"""

from __future__ import annotations

import pytest

from forgecli.application.security.policy_engine import PolicyEngine
from forgecli.application.tool_request.observations import ObservationKind, rejected
from forgecli.domain.agent.actions import ObservationDisposition, ObservationSource
from forgecli.domain.intents import SessionMode
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.findings import AnalysisFindings, RiskFact
from forgecli.domain.security.shell.command_plan import ShellKind
from forgecli.domain.security.shell.parser import parse_command
from forgecli.domain.security.vocabulary import Decision, DecisionReason
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.plan import (
    ExecutionContextRef,
    PlanEffects,
    TargetResolution,
    ToolPlan,
    WorkspaceScope,
)

# ---- 扫描器: `#` 起头的注释不是命令 ----


def _executables(command: str) -> list[str]:
    plan = parse_command(command, ShellKind.POSIX, cwd="/tmp")
    return [unit.executable for unit in plan.units]


def test_a_comment_line_is_not_a_command() -> None:
    """日志里那条命令的形状: cd + 注释行 + sed."""
    command = "cd /tmp\n# 在 SecurityConfig 中添加权限配置\nsed -i '' 's/a/b/' X.java"
    assert _executables(command) == ["cd", "sed"]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("ls -al   # 列出来看看", ["ls"]),
        ("# 头\nls\n# 尾\npwd", ["ls", "pwd"]),
        # 词首才算注释: 下面三条里的 `#` 都是普通字符.
        ("echo a#b", ["echo"]),
        ("curl http://example.com/page#frag", ["curl"]),
        ("grep '#include' main.c", ["grep"]),
    ],
)
def test_only_a_word_initial_hash_starts_a_comment(
    command: str, expected: list[str]
) -> None:
    assert _executables(command) == expected


def test_a_hash_inside_a_heredoc_body_is_left_alone() -> None:
    """heredoc 正文是要执行的脚本内容, 里面的 `#` 归那门语言管, 不归 shell 管."""
    plan = parse_command(
        "cat <<'PY'\n# python 注释\nprint(1)\nPY", ShellKind.POSIX, cwd="/tmp"
    )
    assert [unit.executable for unit in plan.units] == ["cat"]


# ---- 回填: unrunnable 不是策略拒绝 ----


def _plan() -> ToolPlan:
    return ToolPlan(
        plan_id="inv_1",
        tool_name="shell_run",
        spec_hash="spec",
        normalized_input={"command": "ls"},
        capabilities=frozenset({Capability.EXECUTE_SHELL}),
        effects=PlanEffects(child_process=True),
        target_resolution=TargetResolution.UNKNOWN,
        workspace_scope=WorkspaceScope.OUTSIDE,
        execution_context=ExecutionContextRef(cwd="/tmp", environment_hash="env"),
    )


def _context() -> PolicyContext:
    return PolicyContext(
        mode=SessionMode.AUTO,
        session_id="s",
        turn_id="t",
        execution_profile_hash="profile",
    )


def test_unrunnable_stays_a_deny_but_is_told_apart_from_a_policy_deny() -> None:
    """仍然不执行 —— 批准买不到任何东西. 但理由要能被下游分辨出来."""
    findings = AnalysisFindings(plan=_plan()).cannot_run(
        DecisionReason.EXECUTABLE_NOT_FOUND,
        RiskFact(code="executable_not_found", detail="ls: 不在受控 PATH 上"),
    )
    decision = PolicyEngine().decide(findings, _context())
    assert decision.decision is Decision.DENY
    assert decision.unrunnable is DecisionReason.EXECUTABLE_NOT_FOUND
    # 措辞只说我们没接起来: "这条命令无法执行"是一个我们证明不了的结论.
    assert "无法执行" not in decision.message


def test_a_hard_deny_is_not_reported_as_unrunnable() -> None:
    findings = AnalysisFindings(plan=_plan()).denied(
        DecisionReason.HARD_DENY_DESTRUCTIVE,
        RiskFact(code="destructive", detail="rm -rf /"),
    )
    decision = PolicyEngine().decide(findings, _context())
    assert decision.decision is Decision.DENY
    assert decision.unrunnable is None


def test_the_model_is_told_it_may_try_another_command() -> None:
    """换一条命令是对的, 所以 can_retry 必须为真, 而且不能标成安全拒绝.

    `source` 分错的后果是模型把一次环境问题读成"你不该做这件事"; `can_retry` 分错的
    后果是它连换一条都不敢试, 只能把整轮停在这里.
    """
    observation = rejected(
        ObservationKind.COMMAND_UNRUNNABLE,
        "Forge 没能在当前环境里把这条命令接起来",
        invocation_id="inv_1",
        tool_name="shell_run",
        reason_code=DecisionReason.EXECUTABLE_NOT_FOUND.value,
        plan=_plan(),
        risk_facts=(RiskFact(code="executable_not_found", detail="ls: 不在受控 PATH"),),
        can_retry=True,
    )
    rendered = observation.render()
    assert "can_retry: true" in rendered
    # 具体是哪个 token, 为什么接不起来 —— 模型要靠这一行决定换成什么.
    assert "ls: 不在受控 PATH" in rendered
    assert observation.kind.source is ObservationSource.ERROR
    # 计数但不停轮: 一条一条试过去仍然是撞墙, 但一次失败不该作废整轮.
    assert observation.kind.disposition is ObservationDisposition.BLOCKED
