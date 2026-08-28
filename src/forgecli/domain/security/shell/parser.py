"""方言分派与嵌套解释器递归 (ADR-0013 §6.1).

一条命令可能套好几层: `sh -c "cmd /c 'powershell -EncodedCommand ...'"`. 每一层都要
按**它自己的方言**解析, 然后把内层单元一并纳入整体预检. 只看最外层等于没检查.

递归有三个出口, 都不放行:

- 层数超过上限 -> OPAQUE.
- 内层解析失败 -> PARSE_ERROR 向上传播, 不静默丢弃.
- 内层是脚本 (python -c, heredoc) -> 不按 Shell 再解析一遍, 交给 ScriptAnalyzer.

入口只有 `parse_command` 一个: **本实现只接受原始命令串**. 曾经还有一个 `parse_argv`
处理"结构化 argv 请求", 但没有任何工具产出那种请求, 而它承诺的性质 (不经过宿主 shell 的
字符串展开) 只有在执行侧也不经过 shell 时才成立 —— 见 shell_run.py 的模块说明.
"""

from __future__ import annotations

from forgecli.domain.security.shell.cmd import parse_cmd_units
from forgecli.domain.security.shell.command_plan import (
    CommandPlan,
    CommandUnit,
    ParseStatus,
    ShellKind,
    UnitOrigin,
)
from forgecli.domain.security.shell.posix import parse_posix_units
from forgecli.domain.security.shell.powershell import (
    decode_encoded_command,
    parse_powershell_units,
)
from forgecli.domain.security.shell.tokens import ScanError
from forgecli.domain.security.shell.wrappers import (
    NestedCommand,
    indirect_inner_commands,
    nested_command_of,
    strip_prefix,
)

__all__ = ["MAX_WRAPPER_DEPTH", "PARSER_VERSION", "parse_command"]

MAX_WRAPPER_DEPTH = 6
# 进 CommandPlan.command_hash, 而学习规则以 command_hash 为键. 解析结果的形状变了就要
# 加一档, 否则旧规则会继续命中一条**结构已经不同**的命令.
#
# "2": 2026-08-28. IO number 归到重定向上 —— `npm run build 2>&1` 的 argv 从
# ("run", "build", "2") 变成 ("run", "build"), cmd 那边不再产出一条写向名为 `&` 的
# 文件的重定向.
PARSER_VERSION = "2"


def parse_command(
    raw_command: str, shell_kind: ShellKind, *, cwd: str = ""
) -> CommandPlan:
    """解析一条原始 Shell 命令为 CommandPlan."""
    units: list[CommandUnit] = []
    opaque: list[str] = []
    try:
        _expand(raw_command, shell_kind, depth=0, units=units, opaque=opaque)
    except ScanError as exc:
        return CommandPlan(
            shell_kind=shell_kind,
            raw_command=raw_command,
            parser_version=PARSER_VERSION,
            units=tuple(units),
            status=ParseStatus.PARSE_ERROR,
            opaque_constructs=tuple(dict.fromkeys(opaque)),
            parse_error=str(exc),
            cwd=cwd,
        )
    status = ParseStatus.OPAQUE if opaque else ParseStatus.OK
    if not units:
        status = ParseStatus.PARSE_ERROR
    return CommandPlan(
        shell_kind=shell_kind,
        raw_command=raw_command,
        parser_version=PARSER_VERSION,
        units=tuple(units),
        status=status,
        opaque_constructs=tuple(dict.fromkeys(opaque)),
        parse_error=None if units else "命令为空",
        cwd=cwd,
    )


# ---- 内部 ----


def _expand(
    source: str,
    shell_kind: ShellKind,
    *,
    depth: int,
    units: list[CommandUnit],
    opaque: list[str],
    origin: UnitOrigin | None = None,
) -> None:
    if depth > MAX_WRAPPER_DEPTH:
        opaque.append(f"嵌套层数超过 {MAX_WRAPPER_DEPTH}")
        return
    parsed_units, nested = _parse_dialect(source, shell_kind, depth, opaque)
    for unit in parsed_units:
        retagged = unit if origin is None else _retag(unit, origin)
        _expand_unit(retagged, shell_kind, depth=depth, units=units, opaque=opaque)
    for nested_origin, inner in nested:
        _expand(
            inner,
            shell_kind,
            depth=depth + 1,
            units=units,
            opaque=opaque,
            origin=nested_origin,
        )


def _parse_dialect(
    source: str, shell_kind: ShellKind, depth: int, opaque: list[str]
) -> tuple[tuple[CommandUnit, ...], tuple[tuple[UnitOrigin, str], ...]]:
    if shell_kind is ShellKind.POSIX:
        parsed = parse_posix_units(source, depth=depth)
        opaque.extend(parsed.opaque)
        return parsed.units, parsed.nested
    if shell_kind is ShellKind.CMD:
        cmd_parsed = parse_cmd_units(source, depth=depth)
        opaque.extend(cmd_parsed.opaque)
        return cmd_parsed.units, ()
    ps_parsed = parse_powershell_units(source, depth=depth)
    opaque.extend(ps_parsed.opaque)
    return ps_parsed.units, ps_parsed.nested


def _expand_unit(
    unit: CommandUnit,
    shell_kind: ShellKind,
    *,
    depth: int,
    units: list[CommandUnit],
    opaque: list[str],
) -> None:
    """剥离可剥离前缀, 展开嵌套解释器, 最后把单元收进列表."""
    current = unit
    for _ in range(MAX_WRAPPER_DEPTH):
        stripped = strip_prefix(current.executable, current.argv)
        if stripped is None:
            break
        executable, argv, assignments = stripped
        current = CommandUnit(
            executable=executable,
            argv=argv,
            connector=current.connector,
            origin=current.origin,
            redirects=current.redirects,
            assignments=(*current.assignments, *assignments),
            script=current.script,
            depth=current.depth,
            raw=current.raw,
            opaque_reasons=current.opaque_reasons,
        )

    nested = nested_command_of(current.executable, current.argv)
    units.append(current)
    # `find -exec CMD ;` 与 `xargs CMD` 的内层已经是分好词的 argv, 不能拼回字符串再走
    # 一遍 Shell 解析 —— 引号与 `{}` 都会在那一步出错. 所以它与 nested 走两条路.
    units.extend(
        _inner_unit(executable, argv, depth=depth)
        for executable, argv in indirect_inner_commands(
            current.executable, current.argv
        )
    )
    if nested is None:
        return
    _descend(nested, depth=depth, units=units, opaque=opaque)


def _inner_unit(executable: str, argv: tuple[str, ...], *, depth: int) -> CommandUnit:
    """间接执行的内层命令.

    connector 保持 NONE: 它不是与前一个单元并列的一条命令, 而是**被**前一个单元调起的.
    origin 说明了这层关系, 再给它一个 `;` 之类的连接符只会让审批展示看起来像是用户自己
    写了两条命令.
    """
    return CommandUnit(
        executable=executable,
        argv=argv,
        origin=UnitOrigin.WRAPPER_INNER,
        depth=depth + 1,
        raw=" ".join((executable, *argv)),
    )


def _descend(
    nested: NestedCommand,
    *,
    depth: int,
    units: list[CommandUnit],
    opaque: list[str],
) -> None:
    source = nested.source
    if nested.encoded:
        source = decode_encoded_command(source)
    _expand(
        source,
        nested.shell_kind,
        depth=depth + 1,
        units=units,
        opaque=opaque,
        origin=UnitOrigin.WRAPPER_INNER,
    )


def _retag(unit: CommandUnit, origin: UnitOrigin) -> CommandUnit:
    return CommandUnit(
        executable=unit.executable,
        argv=unit.argv,
        connector=unit.connector,
        origin=origin,
        redirects=unit.redirects,
        assignments=unit.assignments,
        script=unit.script,
        depth=unit.depth,
        raw=unit.raw,
        opaque_reasons=unit.opaque_reasons,
    )
