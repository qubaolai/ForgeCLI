"""POSIX Shell 词法扫描 (ADR-0013 §6).

扫描器要同时做四件普通分词器不做的事:

1. **保留引用状态**: `rm "$X"` 与 `rm $X` 的目标展开行为不同, 后者才会二次分词.
2. **提取嵌套结构**: `$(...)`, 反引号, `<(...)` 里的内容是**另一条要执行的命令**, 必须
   单独拿出来递归分析, 而不是当成一个普通参数字符串.
3. **捕获 heredoc 正文**: `python3 - <<'PY' ... PY` 的正文是要执行的脚本内容, 它在语法
   上不在命令行里, 但在语义上是这条命令最危险的部分.
4. **拒绝控制字符**: NUL 与裸控制字符往往用于骗过展示层, 让用户批准的字符串与实际执行
   的不是同一条.

扫描器不做裁决, 也不展开变量和 glob —— 展开需要冻结的文件系统视图, 那是 expansion.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from forgecli.domain.security.shell.command_plan import RedirectKind

__all__ = [
    "REDIRECT_OPERATORS",
    "ScanError",
    "Substitution",
    "SubstitutionKind",
    "Token",
    "TokenKind",
    "default_fd_of",
    "tokenize_posix",
]

# 重定向操作符 -> 种类, 外加各自省略 IO number 时的默认文件描述符.
#
# **全项目唯一一份**: posix.py 原先自带一份键集相同的副本, 而这里要用它判断"`2>` 里
# 的 2 归谁". 两份漂了的后果是扫描器把 2 收进操作符, 解析器却不认这个操作符, 于是那条
# 重定向凭空消失 —— 比现在这个 bug 更难发现.
#
# ADR-0040 B 类表: POSIX XCU 2.7 的重定向操作符, 是封闭集合.
# 表外默认: 不在表里就不是重定向, 前面的数字照旧当普通参数.
# 漏一项的后果: 少认一种重定向, 该单元的写入目标少一条, 方向是更严不是更松.
REDIRECT_OPERATORS: dict[str, tuple[RedirectKind, int]] = {
    "<": (RedirectKind.INPUT, 0),
    ">": (RedirectKind.OUTPUT, 1),
    ">>": (RedirectKind.APPEND, 1),
    "<<": (RedirectKind.HEREDOC, 0),
    "<<-": (RedirectKind.HEREDOC, 0),
    "<<<": (RedirectKind.HERESTRING, 0),
    ">&": (RedirectKind.DUPLICATE, 1),
    "<&": (RedirectKind.DUPLICATE, 0),
}


def default_fd_of(operator: str) -> int:
    """省略 IO number 时这个操作符作用在哪个 fd 上. `> x` 是 1, `< x` 是 0."""
    return REDIRECT_OPERATORS[operator][1]


# 病态嵌套的深度上限: $( $( $( ... ) ) ) 不该把栈打穿.
MAX_NESTING_DEPTH = 8

_OPERATORS: tuple[str, ...] = (
    "<<<",
    "<<-",
    "&&",
    "||",
    ";;",
    "|&",
    ">>",
    ">&",
    "<&",
    "<<",
    "|",
    ";",
    "&",
    "<",
    ">",
    "(",
    ")",
    "\n",
)

_ALLOWED_CONTROL = {"\t", "\n", "\r"}


class ScanError(Exception):
    """词法阶段失败. 上层据此产出 ParseStatus.PARSE_ERROR, 绝不当作空命令放行."""


class TokenKind(Enum):
    WORD = "word"
    OPERATOR = "operator"


class SubstitutionKind(Enum):
    COMMAND = "command"  # $(...) 与反引号
    PROCESS_IN = "process_in"  # <(...)
    PROCESS_OUT = "process_out"  # >(...)
    ARITHMETIC = "arithmetic"  # $((...))


@dataclass(frozen=True)
class Substitution:
    kind: SubstitutionKind
    inner: str


@dataclass
class Token:
    text: str
    kind: TokenKind = TokenKind.WORD
    quoted: bool = False
    # 仅重定向操作符 token 会带: `2>&1` 里那个 2. 见 _Scanner._take_io_number.
    fd: int | None = None
    # 该 token 里嵌的可执行片段. 它们要递归分析, 不能当普通字符串.
    substitutions: tuple[Substitution, ...] = ()
    # 该 token 里出现的未展开变量引用 (目标集合因此可能是 DYNAMIC).
    variables: tuple[str, ...] = ()
    heredoc: str | None = field(default=None)


def tokenize_posix(command: str) -> list[Token]:
    """扫描一条 POSIX 命令. 结构不闭合或含非法控制字符时抛 ScanError."""
    _reject_control_chars(command)
    return _Scanner(command).run()


def _reject_control_chars(command: str) -> None:
    for index, char in enumerate(command):
        if char in _ALLOWED_CONTROL:
            continue
        if ord(char) < 0x20 or ord(char) == 0x7F:
            raise ScanError(f"命令含控制字符 (offset {index}, 0x{ord(char):02x})")


class _Scanner:
    def __init__(self, command: str) -> None:
        self._src = command
        self._pos = 0
        self._tokens: list[Token] = []
        self._buffer = ""
        self._quoted = False
        self._has_content = False
        self._subs: list[Substitution] = []
        self._vars: list[str] = []
        # 等待正文的 heredoc: (分隔符, 是否允许缩进, 是否展开变量).
        self._pending_heredocs: list[tuple[str, bool, bool]] = []
        self._expect_heredoc_word = False
        self._heredoc_strip = False

    def run(self) -> list[Token]:
        while self._pos < len(self._src):
            char = self._src[self._pos]
            if char == "\\":
                self._read_escape()
            elif char == "'":
                self._read_single_quoted()
            elif char == '"':
                self._read_double_quoted()
            elif char == "`":
                self._read_backquoted()
            elif char == "$" and self._peek(1) == "(":
                self._read_dollar_paren()
            elif char == "$" and self._peek(1) == "{":
                self._read_brace_variable()
            elif char == "$":
                self._read_variable()
            elif char in "<>" and self._peek(1) == "(":
                self._read_process_substitution(char)
            elif char in " \t":
                self._flush()
                self._pos += 1
            elif char == "\n":
                self._flush()
                self._emit_operator("\n")
                self._pos += 1
                self._consume_heredoc_bodies()
            else:
                operator = self._match_operator()
                if operator is not None:
                    fd = self._take_io_number(operator)
                    self._flush()
                    self._emit_operator(operator, fd)
                    self._pos += len(operator)
                    if operator in ("<<", "<<-"):
                        self._expect_heredoc_word = True
                        self._heredoc_strip = operator == "<<-"
                else:
                    self._buffer += char
                    self._has_content = True
                    self._pos += 1
        self._flush()
        if self._pending_heredocs:
            # 命令结束了 heredoc 却没收到正文: 结构不闭合, 不能当成空内容继续.
            self._consume_heredoc_bodies()
        return self._tokens

    # ---- 读取各类结构 ----

    def _read_escape(self) -> None:
        nxt = self._peek(1)
        if nxt == "":
            raise ScanError("命令以反斜杠结尾")
        if nxt == "\n":  # 续行
            self._pos += 2
            return
        self._buffer += nxt
        self._has_content = True
        self._quoted = True
        self._pos += 2

    def _read_single_quoted(self) -> None:
        end = self._src.find("'", self._pos + 1)
        if end == -1:
            raise ScanError("单引号未闭合")
        self._buffer += self._src[self._pos + 1 : end]
        self._quoted = True
        self._has_content = True
        self._pos = end + 1

    def _read_double_quoted(self) -> None:
        self._pos += 1
        self._quoted = True
        self._has_content = True
        while self._pos < len(self._src):
            char = self._src[self._pos]
            if char == '"':
                self._pos += 1
                return
            if char == "\\":
                self._read_escape()
                continue
            if char == "`":
                self._read_backquoted()
                continue
            if char == "$" and self._peek(1) == "(":
                self._read_dollar_paren()
                continue
            if char == "$" and self._peek(1) == "{":
                self._read_brace_variable()
                continue
            if char == "$":
                self._read_variable()
                continue
            self._buffer += char
            self._pos += 1
        raise ScanError("双引号未闭合")

    def _read_backquoted(self) -> None:
        end = self._src.find("`", self._pos + 1)
        if end == -1:
            raise ScanError("反引号未闭合")
        inner = self._src[self._pos + 1 : end]
        self._subs.append(Substitution(SubstitutionKind.COMMAND, inner))
        self._buffer += _placeholder(len(self._subs) - 1)
        self._has_content = True
        self._pos = end + 1

    def _read_dollar_paren(self) -> None:
        if self._peek(2) == "(":  # $((...)) 算术展开
            end = _match_balanced(self._src, self._pos + 2, "(", ")")
            inner = self._src[self._pos + 3 : end - 1]
            self._subs.append(Substitution(SubstitutionKind.ARITHMETIC, inner))
        else:
            end = _match_balanced(self._src, self._pos + 1, "(", ")")
            inner = self._src[self._pos + 2 : end - 1]
            self._subs.append(Substitution(SubstitutionKind.COMMAND, inner))
        self._buffer += _placeholder(len(self._subs) - 1)
        self._has_content = True
        self._pos = end

    def _read_process_substitution(self, char: str) -> None:
        end = _match_balanced(self._src, self._pos + 1, "(", ")")
        inner = self._src[self._pos + 2 : end - 1]
        kind = (
            SubstitutionKind.PROCESS_IN if char == "<" else SubstitutionKind.PROCESS_OUT
        )
        self._subs.append(Substitution(kind, inner))
        self._buffer += _placeholder(len(self._subs) - 1)
        self._has_content = True
        self._pos = end

    def _read_brace_variable(self) -> None:
        end = _match_balanced(self._src, self._pos + 1, "{", "}")
        name = self._src[self._pos + 2 : end - 1]
        self._vars.append(name)
        self._buffer += f"${{{name}}}"
        self._has_content = True
        self._pos = end

    def _read_variable(self) -> None:
        self._pos += 1
        name = ""
        while self._pos < len(self._src) and (
            self._src[self._pos].isalnum() or self._src[self._pos] in "_?@*#!$"
        ):
            name += self._src[self._pos]
            self._pos += 1
            if name in ("?", "@", "*", "#", "!", "$"):
                break
        self._vars.append(name)
        self._buffer += f"${name}"
        self._has_content = True

    # ---- token 产出 ----

    def _match_operator(self) -> str | None:
        for operator in _OPERATORS:
            if self._src.startswith(operator, self._pos):
                return operator
        return None

    def _flush(self) -> None:
        if not self._has_content:
            return
        token = Token(
            text=self._buffer,
            kind=TokenKind.WORD,
            quoted=self._quoted,
            substitutions=tuple(self._subs),
            variables=tuple(self._vars),
        )
        self._tokens.append(token)
        if self._expect_heredoc_word:
            delimiter = self._buffer
            expand = not self._quoted
            self._pending_heredocs.append((delimiter, self._heredoc_strip, expand))
            self._expect_heredoc_word = False
        self._buffer = ""
        self._quoted = False
        self._has_content = False
        self._subs = []
        self._vars = []

    def _emit_operator(self, operator: str, fd: int | None = None) -> None:
        self._tokens.append(Token(text=operator, kind=TokenKind.OPERATOR, fd=fd))

    def _take_io_number(self, operator: str) -> int | None:
        """`2>&1` 里的 2 属于重定向, 不是 npm 的参数 (POSIX XCU 2.7 IO_NUMBER).

        **只有扫描器判得了这件事**, 因为判据是"紧挨着, 中间没有空白": 缓冲区一遇到
        空白就会 flush, 所以走到这里时缓冲区里还有东西, 就说明它与操作符之间没有空白.
        到了 token 列表那一层这个区别已经没了 —— `echo 2 >f` 与 `echo 2>f` 的 token
        序列完全相同, 而前者要把 2 交给 echo, 后者不能.

        四个条件缺一不可:

        - 操作符是重定向. `cmd 2 && x` 里的 2 是参数.
        - 未被引用. POSIX 明写 IO number 必须是 unquoted, `echo "2">f` 的 2 是参数.
        - 没有替换与变量引用. `$n>f` 的 fd 要运行期才知道, 静态判不了就别判.
        - 全部是 ASCII 数字. 不用 str.isdigit(): 它对全角数字与上标也返回真,
          而 shell 只认 ASCII —— 那种字符构成的是文件名, 不是 fd.
        """
        if operator not in REDIRECT_OPERATORS:
            return None
        if not self._has_content or self._quoted or self._subs or self._vars:
            return None
        if not all(char in "0123456789" for char in self._buffer):
            return None
        number = int(self._buffer)
        self._buffer = ""
        self._has_content = False
        return number

    def _consume_heredoc_bodies(self) -> None:
        """把 heredoc 正文从源码里取出来, 挂到对应的分隔符 token 上."""
        while self._pending_heredocs:
            delimiter, strip, _expand = self._pending_heredocs.pop(0)
            lines: list[str] = []
            while True:
                newline = self._src.find("\n", self._pos)
                line = (
                    self._src[self._pos : newline]
                    if newline != -1
                    else self._src[self._pos :]
                )
                self._pos = len(self._src) if newline == -1 else newline + 1
                candidate = line.strip() if strip else line
                if candidate == delimiter:
                    break
                lines.append(line[1:] if strip and line.startswith("\t") else line)
                if newline == -1:
                    # 正文没有以分隔符结束: 内容仍然要交出去分析, 但要标成不完整.
                    raise ScanError(f"heredoc 未以 {delimiter} 结束")
            body = "\n".join(lines)
            for token in reversed(self._tokens):
                if token.kind is TokenKind.WORD and token.text == delimiter:
                    token.heredoc = body
                    break

    def _peek(self, offset: int) -> str:
        index = self._pos + offset
        return self._src[index] if index < len(self._src) else ""


def _match_balanced(src: str, start: int, opener: str, closer: str) -> int:
    """从 src[start] 处的 opener 开始找配对的 closer, 返回 closer 的下一位."""
    depth = 0
    index = start
    while index < len(src):
        char = src[index]
        if char == "\\":
            index += 2
            continue
        if char == "'":
            end = src.find("'", index + 1)
            if end == -1:
                raise ScanError("嵌套结构中的单引号未闭合")
            index = end + 1
            continue
        if char == opener:
            depth += 1
            if depth > MAX_NESTING_DEPTH:
                raise ScanError("嵌套层数超过上限")
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise ScanError(f"{opener} 未闭合")


def _placeholder(index: int) -> str:
    """替换嵌套结构后留下的占位符. 用不可能出现在真实路径里的形状, 便于下游识别."""
    return f"\x00sub{index}\x00"
