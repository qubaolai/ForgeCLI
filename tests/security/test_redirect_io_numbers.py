"""`2>&1` 里的 2 属于重定向, 不是命令的参数 (2026-08-28 回归).

改动前的实测:

    npm run build 2>&1  ->  argv=('run', 'build', '2')  redirect=(duplicate, '1', None)
    rm out.txt 2>&1     ->  argv=('out.txt', '2')       redirect=(duplicate, '1', None)
    cmd 下的 2>&1        ->  argv=(..., '2', '1')        redirect=(output, '&', None)

两处后果, 都不在"多问一次"这一档:

- **多出一个不存在的目标.** 目标集合是从 argv 推的, 于是 `rm out.txt 2>&1` 推出一个
  叫 `2` 的删除目标. 围栏兜得住执行, 但 ADR-0015 的快照范围与审批界面的风险摘要都读
  这份集合 —— 一条破坏性命令因此拿到错的恢复范围.
- **cmd 更糟**: `>` 与 `&` 被切开, 于是产出一条"写向名为 `&` 的文件"的重定向, 那是
  一个凭空出现的写入目标.

`Redirect.fd` 这个字段一直存在却从来没有被填过, 所以"这条重定向作用在哪个描述符上"
在整条链路里都答不出来.

判据只有一条, 而且**只有扫描器判得了**: 紧挨着, 中间没有空白. `echo 2 >f` 要把 2 交给
echo, `echo 2>f` 不能, 而两者的 token 序列完全相同.
"""

from __future__ import annotations

import pytest

from forgecli.domain.security.shell.command_plan import (
    CommandUnit,
    RedirectKind,
    ShellKind,
)
from forgecli.domain.security.shell.parser import parse_command


def _unit(command: str, kind: ShellKind = ShellKind.POSIX) -> CommandUnit:
    plan = parse_command(command, kind, cwd="/ws")
    assert plan.units, command
    return plan.units[0]


# ---- IO number 归重定向 ----


@pytest.mark.parametrize(
    ("command", "argv"),
    [
        ("npm run build 2>&1", ("run", "build")),
        ("rm out.txt 2>&1", ("out.txt",)),
        ("cat a.txt 2>/dev/null", ("a.txt",)),
        ("make 2>&1", ()),
        ("ls 1>&2", ()),
        ("cmd 3>>log", ()),
    ],
)
def test_the_io_number_leaves_argv(command: str, argv: tuple[str, ...]) -> None:
    assert _unit(command).argv == argv


@pytest.mark.parametrize(
    ("command", "fd"),
    [
        ("make 2>&1", 2),
        ("ls 1>&2", 1),
        ("cmd 3>>log", 3),
        ("foo 22>bar", 22),
    ],
)
def test_the_io_number_lands_on_the_redirect(command: str, fd: int) -> None:
    assert _unit(command).redirects[0].fd == fd


@pytest.mark.parametrize(
    ("command", "fd"),
    [
        ("ls >out", 1),
        ("ls >>out", 1),
        ("cat <in.txt", 0),
    ],
)
def test_an_omitted_io_number_falls_back_to_the_posix_default(
    command: str, fd: int
) -> None:
    """省略时 `fd` 仍然答得上, 调用方不必再判一次 None."""
    assert _unit(command).redirects[0].fd == fd


# ---- 紧挨着才算 ----


def test_a_blank_before_the_operator_keeps_the_number_as_an_argument() -> None:
    """`echo 2 > f` 把 2 写进 f. 这两条命令的 token 序列相同, 只有扫描器分得开."""
    unit = _unit("echo 2 > f")

    assert unit.argv == ("2",)
    assert unit.redirects[0].fd == 1


def test_a_quoted_number_is_an_argument() -> None:
    """POSIX 明写 IO number 必须 unquoted."""
    assert _unit('echo "2">f').argv == ("2",)


def test_a_variable_before_the_operator_is_not_an_io_number() -> None:
    """`$n>f` 的 fd 要运行期才知道, 静态判不了就别判."""
    assert _unit("x ${n}>f").argv == ("${n}",)


def test_a_number_before_a_connector_is_an_argument() -> None:
    """判据里"操作符必须是重定向"这一条: `echo 2 && ls` 的 2 是 echo 的参数."""
    assert _unit("echo 2 && ls").argv == ("2",)


def test_a_number_glued_to_a_name_is_not_an_io_number() -> None:
    """`foo2>f` 的整个 foo2 是命令名, 不是 foo 加 fd 2."""
    unit = _unit("foo2>f")

    assert unit.executable == "foo2"
    assert unit.redirects[0].fd == 1


def test_non_ascii_digits_are_a_filename_not_an_fd() -> None:
    """str.isdigit() 对全角数字返回真, 而 shell 只认 ASCII."""
    assert _unit("echo \uff12>f").argv == ("\uff12",)


# ---- 目标集合不再多出一个 ----


def test_a_duplicate_redirect_adds_no_write_target() -> None:
    """`2>&1` 复制的是描述符, 没有碰任何文件."""
    assert _unit("rm out.txt 2>&1").write_targets == ()


def test_the_stray_number_is_no_longer_a_target() -> None:
    unit = _unit("rm out.txt 2>&1")

    assert "2" not in unit.argv
    assert "2" not in unit.write_targets


# ---- cmd 方言 ----


@pytest.mark.parametrize(
    ("command", "argv"),
    [
        ("npm run build 2>&1", ("run", "build")),
        ("dir 2>nul", ()),
        ("type a.txt 1>&2", ("a.txt",)),
    ],
)
def test_cmd_takes_the_io_number_too(command: str, argv: tuple[str, ...]) -> None:
    assert _unit(command, ShellKind.CMD).argv == argv


def test_cmd_no_longer_writes_to_a_file_named_ampersand() -> None:
    """改动前 `2>&1` 在 cmd 下被切成 `>` 加一个叫 `&` 的目标."""
    unit = _unit("npm run build 2>&1", ShellKind.CMD)

    assert unit.redirects[0].kind is RedirectKind.DUPLICATE
    assert unit.redirects[0].target == "1"
    assert unit.write_targets == ()


def test_cmd_still_splits_a_trailing_connector() -> None:
    """`>&` 排在 `>` 与 `&` 前面, 但不能因此把 `>out&echo` 也吃掉."""
    plan = parse_command("dir >out&echo hi", ShellKind.CMD, cwd="C:\\ws")

    assert [unit.executable for unit in plan.units] == ["dir", "echo"]
    assert plan.units[0].write_targets == ("out",)


def test_cmd_escaped_number_is_an_argument() -> None:
    """`^` 转义掉的字符不再有操作符含义, 数字也一样."""
    assert _unit("dir ^2>f", ShellKind.CMD).argv == ("2",)
