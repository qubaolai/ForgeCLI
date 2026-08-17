"""PowerShell 方言解析 (ADR-0013 §6.1).

PowerShell 的危险面与 POSIX 不同:

- `&` 是调用运算符 (`& $cmd`), 不是后台执行.
- `.` 是点源加载 (`. .\\evil.ps1`), 会把脚本内容加载进当前作用域.
- `-EncodedCommand` 接 base64 编码的 UTF-16LE 脚本. **必须先解码再分析**, 但解码出来的
  内容只用于分析, 绝不执行.
- 脚本块 `{ ... }` 与子表达式 `$( ... )` 里都是可执行代码.
- `Invoke-Expression` 与 `iex` 是动态求值, 内容通常运行期才确定.

不支持的语法一律标 OPAQUE 交给上层 —— 通用解析器读不懂不等于安全.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass

from forgecli.domain.security.shell.command_plan import (
    CommandUnit,
    Connector,
    UnitOrigin,
)
from forgecli.domain.security.shell.tokens import ScanError
from forgecli.domain.security.shell.wrappers import script_entry_of

__all__ = ["ParsedPowerShell", "decode_encoded_command", "parse_powershell_units"]

_VARIABLE = re.compile(r"\$(?:\{[^}]+\}|[A-Za-z_][A-Za-z0-9_:]*)")

_CONNECTORS: dict[str, Connector] = {
    "&&": Connector.AND,
    "||": Connector.OR,
    ";": Connector.SEMI,
    "|": Connector.PIPE,
    "\n": Connector.NEWLINE,
}

_DYNAMIC = frozenset({"Invoke-Expression", "iex", "IEX"})


@dataclass(frozen=True)
class ParsedPowerShell:
    units: tuple[CommandUnit, ...]
    nested: tuple[tuple[UnitOrigin, str], ...]
    opaque: tuple[str, ...]


def decode_encoded_command(payload: str) -> str:
    """按 PowerShell 规范解码 -EncodedCommand (base64 + UTF-16LE).

    解码只为分析. 解不开时抛 ScanError, 让上层落 PARSE_ERROR —— 解不开的编码命令绝不
    能因为"看不懂"就放行.
    """
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ScanError(f"EncodedCommand 不是合法 base64: {exc}") from exc
    try:
        return raw.decode("utf-16-le")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def parse_powershell_units(command: str, *, depth: int = 0) -> ParsedPowerShell:
    tokens = _tokenize(command)
    units: list[CommandUnit] = []
    nested: list[tuple[UnitOrigin, str]] = []
    opaque: list[str] = []
    words: list[str] = []
    pending = Connector.NONE

    def flush() -> None:
        nonlocal words, pending
        if not words:
            return
        executable = words[0]
        argv = tuple(words[1:])
        reasons: list[str] = []
        if executable in _DYNAMIC:
            reasons.append("Invoke-Expression 动态求值")
        reasons.extend(
            f"未展开变量: {word}" for word in words if _VARIABLE.search(word)
        )
        opaque.extend(reasons)
        units.append(
            CommandUnit(
                executable=executable,
                argv=argv,
                connector=pending,
                origin=(
                    UnitOrigin.TOP_LEVEL if depth == 0 else UnitOrigin.WRAPPER_INNER
                ),
                script=script_entry_of(executable, argv),
                depth=depth,
                raw=" ".join(words),
                opaque_reasons=tuple(dict.fromkeys(reasons)),
            )
        )
        pending = Connector.NONE
        words = []

    for text, kind in tokens:
        if kind == "operator":
            connector = _CONNECTORS.get(text)
            if connector is None:
                opaque.append(f"未支持的操作符: {text}")
                continue
            flush()
            pending = connector
            continue
        if kind == "block":
            nested.append((UnitOrigin.SCRIPT_BLOCK, text))
            continue
        if kind == "subexpression":
            nested.append((UnitOrigin.SUBSTITUTION, text))
            continue
        words.append(text)
    flush()
    return ParsedPowerShell(
        units=tuple(units),
        nested=tuple(nested),
        opaque=tuple(dict.fromkeys(opaque)),
    )


def _tokenize(command: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    buffer = ""
    has_content = False
    index = 0

    def flush() -> None:
        nonlocal buffer, has_content
        if has_content:
            tokens.append((buffer, "word"))
        buffer, has_content = "", False

    while index < len(command):
        char = command[index]
        if char == "`":  # PowerShell 的转义符是反引号
            if index + 1 >= len(command):
                raise ScanError("命令以反引号结尾")
            buffer += command[index + 1]
            has_content = True
            index += 2
            continue
        if char == "'":
            end = command.find("'", index + 1)
            if end == -1:
                raise ScanError("单引号未闭合")
            buffer += command[index + 1 : end]
            has_content = True
            index = end + 1
            continue
        if char == '"':
            end = command.find('"', index + 1)
            if end == -1:
                raise ScanError("双引号未闭合")
            buffer += command[index + 1 : end]
            has_content = True
            index = end + 1
            continue
        if char == "{":
            flush()
            end = _match(command, index, "{", "}")
            tokens.append((command[index + 1 : end - 1], "block"))
            index = end
            continue
        if char == "$" and index + 1 < len(command) and command[index + 1] == "(":
            flush()
            end = _match(command, index + 1, "(", ")")
            tokens.append((command[index + 2 : end - 1], "subexpression"))
            index = end
            continue
        if char in " \t\r":
            flush()
            index += 1
            continue
        matched = next(
            (
                op
                for op in ("&&", "||", "|", ";", "\n")
                if command.startswith(op, index)
            ),
            None,
        )
        if matched is not None:
            flush()
            tokens.append((matched, "operator"))
            index += len(matched)
            continue
        buffer += char
        has_content = True
        index += 1
    flush()
    return tokens


def _match(src: str, start: int, opener: str, closer: str) -> int:
    depth = 0
    index = start
    while index < len(src):
        char = src[index]
        if char == "`":
            index += 2
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise ScanError(f"{opener} 未闭合")
