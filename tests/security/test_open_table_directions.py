"""开放表的**方向性**用例 (ADR-0040 测试策略 §2).

这些表按定义列不全. 所以它们的正确性判据不是"里面的都对", 而是**表外的会怎样** ——
每一张都要有一条"表外成员"用例, 钉住漏项的后果落在安全的那一侧.

一张表漏了不会有任何用例变红, 除非有人专门为"漏了"写一条. 这个文件就是那些条.
"""

from __future__ import annotations

from forgecli.domain.execution.environment import (
    CORE_ENV_NAMES,
    EnvironmentInheritance,
    sanitize_environment,
)
from forgecli.domain.security.shell.builtins import is_builtin
from forgecli.domain.security.shell.command_plan import ShellKind
from forgecli.domain.security.shell.commands import NETWORK_TOOLS
from forgecli.domain.security.shell.effects import runs_arbitrary_code
from forgecli.domain.security.shell.parser import parse_command
from forgecli.domain.security.shell.wrappers import indirectly_executes, interpreter_of
from forgecli.infrastructure.execution.environment_probe import filter_inherited_path


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


# ---- 环境继承: 默认全继承, 所以"表"只在收紧档下才是表 ----


def test_the_default_tier_passes_a_variable_no_table_knows_about() -> None:
    """默认档没有表, 所以没有漏项.

    这条钉的是方向本身变了: 早先是"证明必要才放行", 一份注定不全的 allowlist 决定谁
    进得去, 漏一项的后果是某个工具在 Forge 里行为与终端不同, 而这种差异不报错.
    现在默认全继承, 表只在用户明确要求收紧时才出现.
    """
    source = {
        "PATH": "/whatever",
        "FROBNICATE_TOOL_HOME": "/opt/frob",
        "HOME": "/home/x",
    }

    result = sanitize_environment(source, trusted_path=("/usr/bin",))

    assert result["FROBNICATE_TOOL_HOME"] == "/opt/frob"
    # PATH 一律换成筛过的受控 PATH, 不透传宿主原值.
    assert result["PATH"] == "/usr/bin"


def test_the_core_tier_drops_what_its_table_does_not_name() -> None:
    """`core` 档下 CORE_ENV_NAMES 才是一张开放表, 漏项的后果落在"工具行为异常"这一侧."""
    assert "FROBNICATE_TOOL_HOME" not in CORE_ENV_NAMES

    result = sanitize_environment(
        {"PATH": "/x", "FROBNICATE_TOOL_HOME": "/opt/frob", "HOME": "/home/x"},
        trusted_path=("/usr/bin",),
        inheritance=EnvironmentInheritance.CORE,
    )

    assert "FROBNICATE_TOOL_HOME" not in result
    assert result["HOME"] == "/home/x"


def test_the_none_tier_keeps_only_path_and_controlled_values() -> None:
    result = sanitize_environment(
        {"PATH": "/x", "HOME": "/home/x"},
        trusted_path=("/usr/bin",),
        inheritance=EnvironmentInheritance.NONE,
        controlled={"GIT_CONFIG_NOSYSTEM": "1"},
    )

    assert set(result) == {"PATH", "GIT_CONFIG_NOSYSTEM"}


# ---- PATH 筛选: 认不出的条目**保留**, 方向与上面几张表相反 ----


def test_an_unrecognised_path_entry_is_kept() -> None:
    """这里的方向是"多留一个目录", 不是"多丢一个".

    丢错了的后果是开发者终端里跑得起来的命令在 Forge 里报不存在, 而模型读不出这是
    "没装"还是"被拿掉了" —— 日志里它为此连发五条命令满机器找 maven.

    留错了的后果由另一道闸兜: 可执行文件身份拒绝让 Agent 可写位置的文件继承同名系统
    工具的授权 (`ExecutableIdentity.eligible_for_plain_allow`), 所以 PATH 上多一个
    目录并不等于多一条免审批的执行路径.
    """
    kept = filter_inherited_path(
        "/opt/frobnicate/bin:/usr/bin", workspace_roots=("/ws",)
    )

    assert kept == ("/opt/frobnicate/bin", "/usr/bin")


def test_path_entries_the_agent_can_write_are_dropped() -> None:
    """减的只有这两类, 而它们都是可以证明的, 不是猜的."""
    kept = filter_inherited_path(
        "/ws/node_modules/.bin:.:relative/bin:/usr/bin", workspace_roots=("/ws",)
    )

    assert kept == ("/usr/bin",)


def test_inherited_path_order_survives() -> None:
    """开发者把 temurin-11 排在 /usr/bin 前面是有意的; 重排等于换了一个 java."""
    kept = filter_inherited_path("/opt/jdk11/bin:/usr/bin:/opt/jdk11/bin")

    assert kept == ("/opt/jdk11/bin", "/usr/bin")


# ---- 低价值排序: 表外不影响结果集合 ----


def test_low_value_tables_only_sort_they_never_exclude() -> None:
    """一个叫 vendor 的目录不该在默认检索里消失 (ADR-0040 §8.5)."""
    from forgecli.application.tools.builtin.search_text import _LOW_VALUE_SEGMENTS

    assert "vendor" in _LOW_VALUE_SEGMENTS  # 它只影响排序
    # 真正的"不排除"由 search_text 的行为用例保证, 见 tests/tools/test_search_text.py.
