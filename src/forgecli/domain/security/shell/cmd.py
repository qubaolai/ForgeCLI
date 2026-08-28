"""cmd.exe 方言解析 (ADR-0013 §6.1).

不能拿 POSIX 解析器凑合. 三处语义完全不同:

- `&` 在 cmd 是命令分隔符, 在 POSIX 是后台执行. 按 POSIX 解析 `dir & del x.txt`, 后半
  条命令会从视野里消失.
- `^` 是转义符, `^&` 是字面量 `&`; POSIX 里 `^` 什么都不是.
- 变量是 `%VAR%` 与延迟展开的 `!VAR!`, 不是 `$VAR`.

未展开的变量一律记成 opaque: `del %TARGET%` 的真实目标要到运行期才确定.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple

from forgecli.domain.security.shell.command_plan import (
    CommandUnit,
    Connector,
    Redirect,
    RedirectKind,
    UnitOrigin,
)
from forgecli.domain.security.shell.tokens import ScanError
from forgecli.domain.security.shell.wrappers import script_entry_of

__all__ = ["ParsedCmd", "parse_cmd_units"]

_VARIABLE = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%|!([A-Za-z_][A-Za-z0-9_]*)!")

_CONNECTORS: dict[str, Connector] = {
    "&&": Connector.AND,
    "||": Connector.OR,
    "&": Connector.SEMI,  # cmd 的 & 是"接着执行", 语义上等同 POSIX 的 ;
    "|": Connector.PIPE,
}

# 操作符 -> (种类, 省略 IO number 时的默认 fd). 与 POSIX 那份分开列是因为 cmd 的
# 集合更小: 没有 heredoc, 没有 herestring.
# ADR-0040 B 类表: cmd 的重定向操作符.
# 表外默认: 不在表里就不是重定向, 前面的数字照旧当普通参数.
# 漏一项的后果: 少认一种重定向, 该单元的写入目标少一条, 方向是更严不是更松.
_REDIRECTS: dict[str, tuple[RedirectKind, int]] = {
    "<": (RedirectKind.INPUT, 0),
    ">": (RedirectKind.OUTPUT, 1),
    ">>": (RedirectKind.APPEND, 1),
    ">&": (RedirectKind.DUPLICATE, 1),
}

# 长的排前面: `>>` 必须先于 `>`, `>&` 必须先于 `>` 与 `&`, 否则 `2>&1` 会被切成
# `>` 加一个叫 `&` 的文件名 —— 那正是这张表存在之前的行为.
_CMD_OPERATORS: tuple[str, ...] = (
    ">>",
    ">&",
    "&&",
    "||",
    "&",
    "|",
    "<",
    ">",
    "(",
    ")",
)


class _CmdToken(NamedTuple):
    text: str
    quoted: bool
    # 仅重定向操作符会带: `2>&1` 里那个 2.
    fd: int | None = None


@dataclass(frozen=True)
class ParsedCmd:
    units: tuple[CommandUnit, ...]
    opaque: tuple[str, ...]


def parse_cmd_units(command: str, *, depth: int = 0) -> ParsedCmd:
    tokens = _tokenize(command)
    units: list[CommandUnit] = []
    opaque: list[str] = []
    words: list[tuple[str, bool]] = []
    redirects: list[Redirect] = []
    pending = Connector.NONE

    def flush() -> None:
        nonlocal words, redirects, pending
        if not words and not redirects:
            words, redirects = [], []
            return
        executable = words[0][0] if words else ""
        argv = tuple(word for word, _ in words[1:])
        variables = [f"未展开变量: {word}" for word, has_var in words if has_var]
        opaque.extend(variables)
        units.append(
            CommandUnit(
                executable=executable,
                argv=argv,
                connector=pending,
                origin=(
                    UnitOrigin.TOP_LEVEL if depth == 0 else UnitOrigin.WRAPPER_INNER
                ),
                redirects=tuple(redirects),
                script=script_entry_of(executable, argv),
                depth=depth,
                raw=" ".join((executable, *argv)).strip(),
                opaque_reasons=tuple(dict.fromkeys(variables)),
            )
        )
        pending = Connector.NONE
        words, redirects = [], []

    index = 0
    while index < len(tokens):
        token = tokens[index]
        text, quoted = token.text, token.quoted
        if not quoted and text in _CONNECTORS:
            flush()
            pending = _CONNECTORS[text]
            index += 1
            continue
        if not quoted and text in _REDIRECTS:
            if index + 1 >= len(tokens):
                raise ScanError(f"重定向 {text} 缺少目标")
            kind, default_fd = _REDIRECTS[text]
            redirects.append(
                Redirect(
                    kind=kind,
                    target=tokens[index + 1].text,
                    fd=token.fd if token.fd is not None else default_fd,
                )
            )
            index += 2
            continue
        if not quoted and text in ("(", ")"):
            index += 1
            continue
        words.append((text, bool(_VARIABLE.search(text)) and not quoted))
        index += 1
    flush()
    return ParsedCmd(units=tuple(units), opaque=tuple(dict.fromkeys(opaque)))


def _tokenize(command: str) -> list[_CmdToken]:
    """cmd 分词: 处理 `^` 转义, 双引号, 以及多字符操作符."""
    tokens: list[_CmdToken] = []
    buffer = ""
    quoted = False
    has_content = False
    index = 0

    def flush() -> None:
        nonlocal buffer, quoted, has_content
        if has_content:
            tokens.append(_CmdToken(buffer, quoted))
        buffer, quoted, has_content = "", False, False

    def take_io_number(operator: str) -> int | None:
        """`2>&1` 里的 2 属于重定向, 不是上一条命令的参数.

        判据与 POSIX 扫描器那份相同: 缓冲区一遇到空白就 flush, 所以走到这里缓冲区
        里还有东西就说明它紧挨着操作符. 被引用的 (`^2` 或 `"2"`) 不算 —— 那是文件名.
        """
        nonlocal buffer, quoted, has_content
        if operator not in _REDIRECTS or not has_content or quoted:
            return None
        if not all(char in "0123456789" for char in buffer):
            return None
        number = int(buffer)
        buffer, quoted, has_content = "", False, False
        return number

    while index < len(command):
        char = command[index]
        if char == "^":
            if index + 1 >= len(command):
                raise ScanError("命令以 ^ 结尾")
            buffer += command[index + 1]
            has_content = True
            quoted = True  # 被转义的字符不再有操作符含义
            index += 2
            continue
        if char == '"':
            end = command.find('"', index + 1)
            if end == -1:
                raise ScanError("双引号未闭合")
            buffer += command[index + 1 : end]
            quoted = True
            has_content = True
            index = end + 1
            continue
        if char in " \t\r\n":
            flush()
            index += 1
            continue
        matched = next(
            (op for op in _CMD_OPERATORS if command.startswith(op, index)),
            None,
        )
        if matched is not None:
            fd = take_io_number(matched)
            flush()
            tokens.append(_CmdToken(matched, False, fd))
            index += len(matched)
            continue
        buffer += char
        has_content = True
        index += 1
    flush()
    return tokens
