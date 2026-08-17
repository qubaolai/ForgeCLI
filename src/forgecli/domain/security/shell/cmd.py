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

_REDIRECTS: dict[str, RedirectKind] = {
    "<": RedirectKind.INPUT,
    ">": RedirectKind.OUTPUT,
    ">>": RedirectKind.APPEND,
}


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
        text, quoted = tokens[index]
        if not quoted and text in _CONNECTORS:
            flush()
            pending = _CONNECTORS[text]
            index += 1
            continue
        if not quoted and text in _REDIRECTS:
            if index + 1 >= len(tokens):
                raise ScanError(f"重定向 {text} 缺少目标")
            redirects.append(
                Redirect(kind=_REDIRECTS[text], target=tokens[index + 1][0])
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


def _tokenize(command: str) -> list[tuple[str, bool]]:
    """cmd 分词: 处理 `^` 转义, 双引号, 以及多字符操作符."""
    tokens: list[tuple[str, bool]] = []
    buffer = ""
    quoted = False
    has_content = False
    index = 0

    def flush() -> None:
        nonlocal buffer, quoted, has_content
        if has_content:
            tokens.append((buffer, quoted))
        buffer, quoted, has_content = "", False, False

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
            (
                op
                for op in (">>", "&&", "||", "&", "|", "<", ">", "(", ")")
                if command.startswith(op, index)
            ),
            None,
        )
        if matched is not None:
            flush()
            tokens.append((matched, False))
            index += len(matched)
            continue
        buffer += char
        has_content = True
        index += 1
    flush()
    return tokens
