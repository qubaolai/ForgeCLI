"""一条命令的哪些参数才是路径.

这一组来自一次真实任务的审批框. 它列出的"读取 (6 项)"里有四项是凭空造的:
`grep`, `skipUrl`, `{}`, `;`. 当时的判据只有一句 `not arg.startswith("-")`.

**一份掺假的清单比没有清单更糟**: 用户学会忽略它之后, 真正危险的那一条也跟着被忽略.

用例横跨两层, 所以不放进纯 domain 的 test_command_effects.py:

- `classify_arguments` 与 `-exec` 内层提取在 domain;
- 最终落成 read_paths / write_paths / delete_paths 在 application 的分析器.

两层都断言, 是因为中间任何一层接错线, 单独看某一层都还是绿的.
"""

from __future__ import annotations

from forgecli.application.security.analyzers.shell_analyzer import _effects_of
from forgecli.domain.security.shell.arguments import classify_arguments
from forgecli.domain.security.shell.command_plan import ShellKind, UnitOrigin
from forgecli.domain.security.shell.effects import EffectKind, effect_kind_of
from forgecli.domain.security.shell.parser import parse_command
from forgecli.domain.tool.plan import PlanEffects


def _effects(command: str, *, cwd: str = "/ws") -> PlanEffects:
    plan = parse_command(command, ShellKind.POSIX, cwd=cwd)
    return _effects_of(plan, cwd=cwd, expanded=(), home="/home/u")


def _paths(command: str) -> tuple[str, ...]:
    plan = parse_command(command, ShellKind.POSIX, cwd="/ws")
    return classify_arguments(plan.units[0]).paths


# ---- 模式 / 表达式不是文件 ----


def test_a_search_pattern_is_not_a_file() -> None:
    """`grep -r skipUrl .` 曾经记一条读取 /ws/skipUrl."""
    assert _paths("grep -r skipUrl .") == (".",)
    assert _effects("grep -r skipUrl .").read_paths == ("/ws",)


def test_dash_e_shifts_which_positional_is_the_pattern() -> None:
    """给了 `-e` 之后, 第一个位置参数就是文件而不是模式.

    不看这一条会把**真实文件**从清单里漏掉 —— 那是往危险方向错, 比多报严重得多.
    """
    assert _effects("grep -e foo bar.txt").read_paths == ("/ws/bar.txt",)


def test_a_sed_script_is_not_a_write_target() -> None:
    """`sed -i.bak s/a/b/ f.txt` 曾经记一条写入 /ws/s/a/b.

    写入侧的多报比读取侧危险: 它会撑大恢复层的快照范围, 同时把一条命令说得比实际更具
    破坏性.
    """
    assert _effects("sed -i.bak s/a/b/ f.txt").write_paths == ("/ws/f.txt",)


def test_find_predicates_are_not_paths() -> None:
    """find 的起点写在第一个选项之前, 之后整段是表达式."""
    assert _effects('find . -name "*.tmp" -type f').read_paths == ("/ws",)


def test_commands_without_path_arguments_produce_none() -> None:
    """`echo hello` 曾经记一条读取 /ws/hello, `sleep 5` 记一条 /ws/5."""
    assert _effects("echo hello").read_paths == ()
    assert _effects("sleep 5").read_paths == ()


def test_an_option_value_glued_with_equals_is_handled() -> None:
    """`--include=*.yml` 的值在同一个 token 里, 不该吃掉下一个参数."""
    assert _effects("grep -r --include=*.yml skipUrl src").read_paths == ("/ws/src",)


# ---- 间接执行的内层命令 ----


def test_exec_inner_command_becomes_its_own_unit() -> None:
    """`find . -exec rm {} ;` 曾经只有一个 find 单元.

    于是 `rm`, `{}`, `;` 变成三个位置参数被当成读取路径, 而审批框最显眼的那行写着
    "删除 0 · 写入 0" —— 一条会删文件的命令, 用户读到的是它什么都不删.
    """
    plan = parse_command("find . -name '*.tmp' -exec rm {} ;", ShellKind.POSIX)
    inner = [unit for unit in plan.units if unit.origin is UnitOrigin.WRAPPER_INNER]

    assert [unit.name for unit in inner] == ["rm"]
    assert effect_kind_of(inner[0]) is EffectKind.DELETE


def test_multiple_exec_predicates_are_all_extracted() -> None:
    """一条 find 可以带多个 -exec. 只取第一个等于漏掉后面那些.

    这里的 `\\;` 必须转义 —— 裸 `;` 在 Shell 里是命令分隔符, 那样写出来的是两条命令,
    而不是一条带两个 -exec 的 find. 真实世界里也只能这么写.
    """
    plan = parse_command(r"find . -exec cp {} /tmp \; -exec rm {} +", ShellKind.POSIX)
    inner = [unit for unit in plan.units if unit.origin is UnitOrigin.WRAPPER_INNER]

    assert [unit.name for unit in inner] == ["cp", "rm"]


def test_a_quoted_terminator_works_the_same() -> None:
    """`';'` 与 `\\;` 是同一个意思, 两种写法都得认."""
    plan = parse_command("find . -exec rm {} ';'", ShellKind.POSIX)
    inner = [unit for unit in plan.units if unit.origin is UnitOrigin.WRAPPER_INNER]

    assert [unit.name for unit in inner] == ["rm"]


def test_the_exec_placeholder_is_not_a_path() -> None:
    """`{}` 代表"find 找到的那个文件", 它自己不是任何路径."""
    effects = _effects("find . -exec rm {} ;")

    assert not any("{}" in path for path in effects.delete_paths)
    assert not any("{}" in path for path in effects.read_paths)


def test_xargs_runs_a_command_not_a_file() -> None:
    """`xargs rm -rf build` 曾经记一条读取 /ws/rm."""
    effects = _effects("printf x | xargs rm -rf build")

    assert "/ws/rm" not in effects.read_paths
    assert effects.delete_paths == ("/ws/build",)


def test_xargs_own_options_are_skipped() -> None:
    """`xargs -n 1 -I {} rm -rf {}`: 要跑的命令是 rm, 不是 1 也不是 {}."""
    plan = parse_command("printf x | xargs -n 1 -I {} rm -rf {}", ShellKind.POSIX)
    inner = [unit for unit in plan.units if unit.origin is UnitOrigin.WRAPPER_INNER]

    assert [unit.name for unit in inner] == ["rm"]


# ---- 表外命令仍然多报 ----


def test_an_unknown_command_still_reports_its_positionals() -> None:
    """少收一条的后果是 `cat ~/.ssh/id_rsa` 这类目标从受保护路径检查里消失.

    多收一条的后果只是展示层要说清它是推测. 两种错法的代价不对称, 所以默认仍然多收 ——
    这个模块不是安全白名单, 表里有没有一条命令只影响它的参数怎么解读.
    """
    effects = _effects("mytool /etc/passwd")

    assert "/etc/passwd" in effects.read_paths
    assert (
        classify_arguments(
            parse_command("mytool /etc/passwd", ShellKind.POSIX).units[0]
        ).known
        is False
    )


def test_a_home_target_of_an_unknown_command_still_reaches_the_check() -> None:
    """这是"宁可多报"最要紧的那个场景, 单独钉住."""
    effects = _effects("mytool ~/.ssh/id_rsa")

    assert "/home/u/.ssh/id_rsa" in effects.read_paths
