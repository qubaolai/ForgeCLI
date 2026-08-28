"""POSIX Shell 解析: token -> CommandUnit (ADR-0013 §6).

把 `cat a.txt | grep b && rm -rf /` 变成三个分析单元, 并保留连接符 —— 第三个单元命中
Hard Deny 时, 整条命令在任何子进程启动前被拒绝.

复合结构 (for / while / if / case / 函数定义) 的处理原则: 关键字本身不是命令, 但它们
**体内的命令仍然要分析**. 因此这里跳过关键字, 继续解析体内的简单命令, 而不是把整段标成
OPAQUE —— 那会让 `for f in *; do rm $f; done` 里的 rm 从视野里消失.
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.security.shell.command_plan import (
    CommandUnit,
    Connector,
    Redirect,
    RedirectKind,
    UnitOrigin,
)
from forgecli.domain.security.shell.tokens import (
    REDIRECT_OPERATORS,
    ScanError,
    Substitution,
    SubstitutionKind,
    Token,
    TokenKind,
    default_fd_of,
    tokenize_posix,
)
from forgecli.domain.security.shell.wrappers import script_entry_of

__all__ = ["ParsedPosix", "parse_posix_units"]

# 复合命令关键字: 跳过关键字本身, 继续解析体内的命令.
_KEYWORDS = frozenset(
    {
        "if",
        "then",
        "elif",
        "else",
        "fi",
        "for",
        "while",
        "until",
        "do",
        "done",
        "case",
        "esac",
        "in",
        "select",
        "function",
        "{",
        "}",
        "!",
        "[[",
        "]]",
    }
)

# 这些关键字后面跟的是**头部**而不是命令 (循环变量, 待匹配值), 整段丢弃.
_HEADER_KEYWORDS = frozenset({"for", "select", "case"})

_CONNECTORS: dict[str, Connector] = {
    "&&": Connector.AND,
    "||": Connector.OR,
    ";": Connector.SEMI,
    ";;": Connector.SEMI,
    "|": Connector.PIPE,
    "|&": Connector.PIPE_AMP,
    "&": Connector.BACKGROUND,
    "\n": Connector.NEWLINE,
}


@dataclass(frozen=True)
class ParsedPosix:
    units: tuple[CommandUnit, ...]
    # 嵌套片段: (来源, 内层源码), 由上层按同一方言递归解析.
    nested: tuple[tuple[UnitOrigin, str], ...]
    opaque: tuple[str, ...]


def parse_posix_units(command: str, *, depth: int = 0) -> ParsedPosix:
    """解析一段 POSIX 命令为分析单元. 扫描失败时抛 ScanError."""
    return _Builder(tokenize_posix(command), depth).run()


class _Builder:
    def __init__(self, tokens: list[Token], depth: int) -> None:
        self._tokens = tokens
        self._depth = depth
        self._units: list[CommandUnit] = []
        self._nested: list[tuple[UnitOrigin, str]] = []
        self._opaque: list[str] = []
        self._words: list[Token] = []
        self._redirects: list[Redirect] = []
        self._heredoc: str | None = None
        self._pending_connector = Connector.NONE

    def run(self) -> ParsedPosix:
        index = 0
        while index < len(self._tokens):
            token = self._tokens[index]
            if token.kind is TokenKind.OPERATOR:
                index = self._handle_operator(token, index)
                continue
            self._collect_substitutions(token)
            self._words.append(token)
            index += 1
        self._flush_unit()
        return ParsedPosix(
            units=tuple(self._units),
            nested=tuple(self._nested),
            opaque=tuple(dict.fromkeys(self._opaque)),
        )

    # ---- 操作符 ----

    def _handle_operator(self, token: Token, index: int) -> int:
        text = token.text
        if text in REDIRECT_OPERATORS:
            return self._handle_redirect(token, index)
        if text in ("(", ")"):
            # 子 shell 的括号本身不是命令; 体内命令与外层同级分析.
            return index + 1
        connector = _CONNECTORS.get(text)
        if connector is None:
            self._opaque.append(f"未支持的操作符: {text}")
            return index + 1
        self._flush_unit()
        self._pending_connector = connector
        return index + 1

    def _handle_redirect(self, token: Token, index: int) -> int:
        operator = token.text
        kind, _ = REDIRECT_OPERATORS[operator]
        # 写明了就用写明的, 省略就用该操作符的默认 fd. 于是 `fd` 永远答得上
        # "这条重定向作用在哪个描述符上", 不必让调用方再判一次 None.
        fd = token.fd if token.fd is not None else default_fd_of(operator)
        target_index = index + 1
        if target_index >= len(self._tokens):
            raise ScanError(f"重定向 {operator} 缺少目标")
        target = self._tokens[target_index]
        if kind is RedirectKind.HEREDOC:
            if target.heredoc is None:
                self._opaque.append("heredoc 正文缺失")
            self._heredoc = target.heredoc or ""
        elif kind is RedirectKind.HERESTRING:
            self._heredoc = target.text
        else:
            self._collect_substitutions(target)
            self._redirects.append(Redirect(kind=kind, target=target.text, fd=fd))
        return target_index + 1

    # ---- 单元产出 ----

    def _flush_unit(self) -> None:
        words = list(self._words)
        assignments: list[tuple[str, str]] = []
        while words and _is_assignment(words[0]):
            name, _, value = words.pop(0).text.partition("=")
            assignments.append((name, value))
        dropped: list[str] = []
        while words and words[0].text in _KEYWORDS:
            dropped.append(words.pop(0).text)
        if any(keyword in _HEADER_KEYWORDS for keyword in dropped):
            # `for f in *.tmp` 的头部不是命令: 循环变量 f 会被当成可执行文件, 平白多出
            # 一个未知命令进裁决. 体内的真命令由后面的 `;` / do 分段单独产出.
            self._reset()
            return

        if not words and not self._redirects and self._heredoc is None:
            self._reset()
            return

        connector = self._pending_connector
        self._pending_connector = Connector.NONE
        executable = words[0].text if words else ""
        argv = tuple(word.text for word in words[1:])
        self._units.append(
            CommandUnit(
                executable=executable,
                argv=argv,
                connector=connector,
                origin=(
                    UnitOrigin.TOP_LEVEL if self._depth == 0 else UnitOrigin.SUBSHELL
                ),
                redirects=tuple(self._redirects),
                assignments=tuple(assignments),
                script=script_entry_of(executable, argv, heredoc=self._heredoc),
                depth=self._depth,
                raw=" ".join((executable, *argv)).strip(),
                opaque_reasons=self._opaque_reasons(words),
            )
        )
        self._reset()

    def _reset(self) -> None:
        self._words = []
        self._redirects = []
        self._heredoc = None

    def _collect_substitutions(self, token: Token) -> None:
        for substitution in token.substitutions:
            if substitution.kind is SubstitutionKind.ARITHMETIC:
                continue
            self._nested.append((_origin_of(substitution), substitution.inner))

    def _opaque_reasons(self, words: list[Token]) -> tuple[str, ...]:
        """未展开的变量让目标集合无法静态封闭 —— 记下来, 由目标解析状态消费."""
        reasons = [
            f"未展开变量: {' '.join(word.variables)}"
            for word in words
            if word.variables
        ]
        return tuple(dict.fromkeys(reasons))


def _origin_of(substitution: Substitution) -> UnitOrigin:
    if substitution.kind is SubstitutionKind.COMMAND:
        return UnitOrigin.SUBSTITUTION
    return UnitOrigin.PROCESS_SUBSTITUTION


def _is_assignment(token: Token) -> bool:
    text = token.text
    if "=" not in text or text.startswith("="):
        return False
    return text.split("=", 1)[0].isidentifier()
