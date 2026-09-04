"""accept_edits 按**这条命令碰了什么**裁决 (ADR-0046).

改之前的判据是"它是不是一次 shell 调用": `EXECUTE_SHELL` 由分析器无条件加上, 而它只在
`fence.automatic_shell` 时才自动放行, 于是 accept_edits 下 `cat README.md` 与
`rm -rf /tmp` 得到同一个结论. 分析器算出的能力集合 (前者 workspace_read, 后者
external_write) 全程没被用上.

这一组钉的是那份精度确实被用上了, 以及三条边界没有被顺手放宽:

- auto 档的行为一个字都不能变 (它的语义是"有围栏时不必猜", ADR-0030 决策 1);
- 没有围栏时一律落回 ASK (ADR-0030 决策 5);
- 目标集合封不住时不放行 —— 那正是"看得清才放行"的字面意思.
"""

from __future__ import annotations

import pytest

from forgecli.domain.execution.fence import fence_for
from forgecli.domain.intents import ApprovalPolicy, SandboxLevel, SessionMode
from forgecli.domain.security.budget import capabilities_requiring_approval
from forgecli.domain.tool.capability import Capability

ROOTS = ("/ws",)

# 分析器对这些命令的实测产出 (见 ADR-0046 的证据表).
READ_IN_WORKSPACE = frozenset(
    {Capability.EXECUTE_SHELL, Capability.SPAWN_PROCESS, Capability.WORKSPACE_READ}
)
WRITE_IN_WORKSPACE = frozenset(
    {Capability.EXECUTE_SHELL, Capability.SPAWN_PROCESS, Capability.WORKSPACE_WRITE}
)
DELETE_IN_WORKSPACE = frozenset(
    {Capability.EXECUTE_SHELL, Capability.SPAWN_PROCESS, Capability.WORKSPACE_DELETE}
)
OPAQUE_RUN = WRITE_IN_WORKSPACE | {Capability.EXECUTE_SCRIPT}
NETWORK = READ_IN_WORKSPACE | {Capability.NETWORK_ACCESS}
OUTSIDE_READ = frozenset(
    {Capability.EXECUTE_SHELL, Capability.SPAWN_PROCESS, Capability.EXTERNAL_READ}
)


def _needs_approval(
    capabilities: frozenset[Capability],
    mode: SessionMode,
    *,
    confined: bool = True,
    targets_closed: bool = True,
) -> frozenset[Capability]:
    return capabilities_requiring_approval(
        capabilities,
        fence_for(mode, workspace_roots=ROOTS),
        confined=confined,
        targets_closed=targets_closed,
    )


# ---- accept_edits: 工作区内 + 看得清 = 自动 ----


@pytest.mark.parametrize(
    ("label", "capabilities"),
    [
        ("cat README.md", READ_IN_WORKSPACE),
        ("sed -i x README.md", WRITE_IN_WORKSPACE),
        ("rm -rf build", DELETE_IN_WORKSPACE),
    ],
)
def test_a_transparent_command_inside_the_workspace_runs_unattended(
    label: str, capabilities: frozenset[Capability]
) -> None:
    assert _needs_approval(capabilities, SessionMode.ACCEPT_EDITS) == frozenset(), label


# ---- accept_edits: 三种"看不清"仍然要问 ----


@pytest.mark.parametrize(
    ("label", "capabilities", "expected"),
    [
        ("npm test", OPAQUE_RUN, Capability.EXECUTE_SCRIPT),
        ("curl https://x", NETWORK, Capability.NETWORK_ACCESS),
        ("cat ~/.ssh/id_rsa", OUTSIDE_READ, Capability.EXTERNAL_READ),
    ],
)
def test_what_forge_cannot_see_still_asks(
    label: str, capabilities: frozenset[Capability], expected: Capability
) -> None:
    assert expected in _needs_approval(capabilities, SessionMode.ACCEPT_EDITS), label


def test_an_unresolved_target_set_still_asks() -> None:
    """`cat $SOMEVAR`: 目标推不出来, 于是连 read 能力都 derive 不出来.

    它看上去比 `cat README.md` 还干净 —— 能力集合更小. 判据只看能力集合的话, 恰恰是
    这一类会溜过去, 所以 accept_edits 这一档额外要求目标已封闭.
    """
    unresolved = frozenset({Capability.EXECUTE_SHELL, Capability.SPAWN_PROCESS})
    assert _needs_approval(
        unresolved, SessionMode.ACCEPT_EDITS, targets_closed=False
    ) == frozenset({Capability.EXECUTE_SHELL})


# ---- 三条不能被顺手放宽的边界 ----


@pytest.mark.parametrize("capabilities", [READ_IN_WORKSPACE, OPAQUE_RUN, NETWORK])
def test_auto_is_unchanged(capabilities: frozenset[Capability]) -> None:
    """auto 的语义是"有围栏时不必猜": 看不透的也放行, 也不看目标封没封闭."""
    assert _needs_approval(capabilities, SessionMode.AUTO) == frozenset()
    assert (
        _needs_approval(capabilities, SessionMode.AUTO, targets_closed=False)
        == frozenset()
    )


@pytest.mark.parametrize("mode", [SessionMode.ACCEPT_EDITS, SessionMode.AUTO])
def test_without_a_real_fence_everything_falls_back_to_asking(
    mode: SessionMode,
) -> None:
    """UNCONFINED 时不退回去证明, 而是逐项确认 (ADR-0030 决策 5)."""
    assert Capability.EXECUTE_SHELL in _needs_approval(
        READ_IN_WORKSPACE, mode, confined=False
    )


def test_the_read_only_tier_never_grants_automatic_shell() -> None:
    """plan 档的语义是只看不改, 那里自动起子进程没有用例.

    目录门本来就不给它 shell_run, 但围栏策略自己也要说得通 —— 一道门的正确性不该
    靠另一道门兜着.
    """
    plan_like = SessionMode(SandboxLevel.READ_ONLY, ApprovalPolicy.ALWAYS)
    assert not fence_for(plan_like, workspace_roots=ROOTS).automatic_shell
    assert Capability.EXECUTE_SHELL in _needs_approval(READ_IN_WORKSPACE, plan_like)


def test_the_two_tiers_are_distinct_flags() -> None:
    """accept_edits 拿到看得清的那一档, auto 两档都拿到."""
    accept = fence_for(SessionMode.ACCEPT_EDITS, workspace_roots=ROOTS)
    auto = fence_for(SessionMode.AUTO, workspace_roots=ROOTS)
    assert (accept.automatic_shell, accept.automatic_opaque_execution) == (True, False)
    assert (auto.automatic_shell, auto.automatic_opaque_execution) == (True, True)


def test_switching_tiers_invalidates_old_authorizations() -> None:
    """两个字段都进 policy_hash: 切档之后旧授权不能沿用到自主性更高的边界."""
    accept = fence_for(SessionMode.ACCEPT_EDITS, workspace_roots=ROOTS)
    auto = fence_for(SessionMode.AUTO, workspace_roots=ROOTS)
    assert accept.policy_hash != auto.policy_hash
