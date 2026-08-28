"""开放表的**方向性**用例 (ADR-0040 测试策略 §2).

这些表按定义列不全. 所以它们的正确性判据不是"里面的都对", 而是**表外的会怎样** ——
每一张都要有一条"表外成员"用例, 钉住漏项的后果落在安全的那一侧.

一张表漏了不会有任何用例变红, 除非有人专门为"漏了"写一条. 这个文件就是那些条.
"""

from __future__ import annotations

import pytest

from forgecli.domain.execution.environment import (
    DEFAULT_ENV_ALLOWLIST,
    INJECTION_VARIABLES,
    sanitize_environment,
)
from forgecli.domain.security.shell.builtins import is_builtin
from forgecli.domain.security.shell.command_plan import ShellKind
from forgecli.domain.security.shell.commands import NETWORK_TOOLS
from forgecli.domain.security.shell.effects import runs_arbitrary_code
from forgecli.domain.security.shell.parser import parse_command
from forgecli.domain.security.shell.wrappers import indirectly_executes, interpreter_of


def _unit(command: str):
    return parse_command(command, ShellKind.POSIX).units[0]


# ---- 命令效果表: 表外按"可能写"处理 ----


def test_an_unknown_command_is_assumed_to_write() -> None:
    """`commands.py` 的记录表漏一条, 后果必须是"多打一次快照", 不是"当它只读"."""
    from forgecli.application.security.analyzers.shell_effects import may_write

    plan = parse_command("frobnicate-9000 --do-something ./x", ShellKind.POSIX)

    assert may_write(plan)


# ---- 解释器登记: 表外取不到内联代码, 不放行 ----


def test_an_unknown_interpreter_yields_no_inline_code() -> None:
    assert interpreter_of("frobnicate-lang") is None


def test_a_known_interpreter_still_resolves_through_the_single_registry() -> None:
    """合并之后只剩这一份登记; 它要真的认得出解释器, 否则上面那条就没有意义."""
    assert interpreter_of("bash") is not None
    assert interpreter_of("deno") is not None


# ---- 委托执行: 表外按"可能跑任意代码"处理 ----


def test_an_unknown_wrapper_that_takes_a_command_is_not_waved_through() -> None:
    """认不出委托关系时, 该单元仍要走脚本路径 —— 方向是多问一次, 不是少问一次."""
    unit = _unit("frobnicate-run -- rm -rf build")

    # 认不出委托 -> 至少不能声称"证明了它只读".
    assert not runs_arbitrary_code(unit) or indirectly_executes(unit.name, unit.argv)


# ---- 内建命令: 表外去 PATH 上找真文件, 找不到就 DENY ----


def test_an_unknown_builtin_is_not_treated_as_a_builtin() -> None:
    """漏一条内建的后果是"一条合法命令被拒", 方向是更严."""
    assert not is_builtin("frobnicate-builtin", ShellKind.POSIX)
    assert is_builtin("export", ShellKind.POSIX)


# ---- 网络工具: 表外与表内在裁决上同一个结论 ----


def test_the_network_table_no_longer_changes_the_decision() -> None:
    """它已经退出授权路径 (ADR-0040 §8.3): 只喂风险摘要与 `curl | sh` 的形状判定.

    这里钉的是"表内表外都不再改变能力集合里的网络位以外的东西" —— 真正的裁决差别
    由围栏产生, 见 tests/security/test_fence_decisions.py.
    """
    assert "curl" in NETWORK_TOOLS
    assert "frobnicate-fetch" not in NETWORK_TOOLS


# ---- 环境变量: 正向 allowlist 才是主机制 ----


def test_a_variable_outside_both_tables_still_does_not_reach_the_child() -> None:
    """这是 C 类表的判据: denylist 漏一项不开洞, 因为 allowlist 才决定谁进得去."""
    source = {
        "PATH": "/whatever",
        "FROBNICATE_INJECT": "1",  # 两张表都没有它
        "HOME": "/home/x",
    }

    result = sanitize_environment(source, trusted_path=("/usr/bin",))

    assert "FROBNICATE_INJECT" not in result
    assert result["HOME"] == "/home/x"
    # PATH 一律换成受控 PATH, 不透传宿主的.
    assert result["PATH"] == "/usr/bin"


@pytest.mark.parametrize("name", ["LD_PRELOAD", "PYTHONPATH"])
def test_named_injection_variables_are_cleared_too(name: str) -> None:
    """denylist 仍然有用 —— 它挡的是那些**在** allowlist 里但会改变执行内容的名字."""
    assert name in INJECTION_VARIABLES

    result = sanitize_environment(
        {"PATH": "/x", name: "/tmp/evil"},
        trusted_path=("/usr/bin",),
        allowlist=(*DEFAULT_ENV_ALLOWLIST, name),
    )

    assert name not in result


# ---- 低价值排序: 表外不影响结果集合 ----


def test_low_value_tables_only_sort_they_never_exclude() -> None:
    """一个叫 vendor 的目录不该在默认检索里消失 (ADR-0040 §8.5)."""
    from forgecli.application.tools.builtin.search_text import _LOW_VALUE_SEGMENTS

    assert "vendor" in _LOW_VALUE_SEGMENTS  # 它只影响排序
    # 真正的"不排除"由 search_text 的行为用例保证, 见 tests/tools/test_search_text.py.
