"""包装器, 解释器与脚本入口的识别表 (ADR-0013 §6.1 / §7).

三类东西必须区分清楚, 混起来就会出安全事故:

- 可剥离前缀 (`env`, `timeout`, `nohup`): 剥掉之后内层才是真正要跑的命令. 不剥,
  分类就会把整条命令当成未知的 `timeout`.
- 不可剥离前缀 (`sudo`, `doas`, `su`): 它们本身就是提权信号, 剥掉等于把 Hard Deny
  的判据丢了.
- 环境运行器与间接执行 (`xargs`, `find -exec`, `eval`): 外层的授权不能传导给
  内层, 内层要作为独立单元重新裁决.

脚本入口不按测试框架穷举. `pytest`, `npm test`, `python3 test.py`, `bash verify.sh`
统一归 EXECUTE_SCRIPT: 它们都能执行任意项目代码, 而框架的种类没有尽头.

**选项按语法族匹配, 不按 token 相等匹配.** 这是这个模块最容易写错的地方: 早先的实现
把选项存成规范名再做 `arg in flags`, 于是 `bash -lc 'rm -rf /'` 认不出 `-c` ——
内层根本没被解析, Hard Deny 看不到 `rm -rf /`, 整条命令拿到 ALLOW. 真实 CLI 的选项
有四种语法: 短选项聚合 (`-lc`), 附加值 (`--eval=X`), 唯一前缀缩写 (PowerShell 的
`-Comm`), 以及大小写不敏感加 `:` 分隔 (PowerShell / cmd). 靠加条目补不上, 因为要补的
是短选项的幂集.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from forgecli.domain.security.shell.command_plan import (
    ScriptPayload,
    ShellKind,
    normalize_executable,
)

__all__ = [
    "INDIRECT_EXECUTORS",
    "PRIVILEGE_ESCALATORS",
    "FlagStyle",
    "Interpreter",
    "NestedCommand",
    "inline_code_of",
    "interpreter_of",
    "nested_command_of",
    "script_entry_of",
    "strip_prefix",
]


class FlagStyle(Enum):
    """选项语法族. 决定怎么在 argv 里找到内联代码."""

    # 短选项可聚合: `-lc` 等价于 `-l -c`. 值可以紧跟在簇里 (`-cCODE`) 或占下一个参数.
    POSIX = "posix"
    # 长选项可带 `=`: `--eval=CODE`, 也接受 `--eval CODE` 与短形式 `-e CODE`.
    GNU = "gnu"
    # 唯一前缀绑定, 大小写不敏感, 支持 `-Command:CODE`.
    POWERSHELL = "powershell"
    # `/c` `/k`, 大小写不敏感, 且 `/c` 之后的**全部**参数都是命令行.
    CMD = "cmd"


@dataclass(frozen=True)
class Interpreter:
    """一个解释器的全部已知事实.

    合成一条记录而不是几张平行的 dict: 早先 `_INTERPRETERS` 与 `_INLINE_FLAGS` 是两个
    键集完全相同的字典, 加一个解释器要改两处, 漏一处的后果是静默的 (`osascript -e`
    就是这么漏掉的).
    """

    language: str
    # POSIX 风格填单字符 ("c"), 其余风格填完整选项名 ("eval", "Command").
    inline_flags: tuple[str, ...] = ()
    style: FlagStyle = FlagStyle.POSIX
    # POSIX 短选项里会吃掉一个值的字符. 不列全会把 `python -mc x` 误判成内联代码 ——
    # 那条命令其实是 `-m c`, 模块名叫 c.
    value_taking: str = ""
    # 内容是 base64 的选项.
    encoded_flags: tuple[str, ...] = ()
    # 以子命令形式触发内联代码 (`deno eval CODE`).
    inline_subcommands: tuple[str, ...] = ()


_SHELL = ("c",)

_INTERPRETERS: dict[str, Interpreter] = {
    "sh": Interpreter("shell", _SHELL, value_taking="o"),
    "bash": Interpreter("shell", _SHELL, value_taking="o"),
    "zsh": Interpreter("shell", _SHELL, value_taking="o"),
    "dash": Interpreter("shell", _SHELL, value_taking="o"),
    "ksh": Interpreter("shell", _SHELL, value_taking="o"),
    "python": Interpreter("python", ("c",), value_taking="mWXQ"),
    "python2": Interpreter("python", ("c",), value_taking="mWXQ"),
    "python3": Interpreter("python", ("c",), value_taking="mWXQ"),
    "node": Interpreter("javascript", ("e", "eval", "p", "print"), style=FlagStyle.GNU),
    "deno": Interpreter(
        "javascript", ("eval",), style=FlagStyle.GNU, inline_subcommands=("eval",)
    ),
    "bun": Interpreter("javascript", ("e", "eval"), style=FlagStyle.GNU),
    "ruby": Interpreter("ruby", ("e",), value_taking="Ir"),
    "perl": Interpreter("perl", ("e", "E"), value_taking="IMmF"),
    "php": Interpreter("php", ("r",), value_taking="d"),
    "pwsh": Interpreter(
        "powershell",
        ("Command", "EncodedCommand"),
        style=FlagStyle.POWERSHELL,
        encoded_flags=("EncodedCommand",),
    ),
    "powershell": Interpreter(
        "powershell",
        ("Command", "EncodedCommand"),
        style=FlagStyle.POWERSHELL,
        encoded_flags=("EncodedCommand",),
    ),
    "cmd": Interpreter("cmd", ("c", "k"), style=FlagStyle.CMD),
    # macOS 上比 `bash -c` 能力更大: 直接跑 AppleScript 或 JXA.
    "osascript": Interpreter("applescript", ("e",), value_taking="ls"),
}


@dataclass(frozen=True)
class StripRule:
    """一个可剥离前缀自身要消耗掉多少参数."""

    mode: str
    # 会吃掉下一个参数的选项. 按前缀分开列: `exec -a NAME` 吃一个,
    # 而 `exec -c` (清空环境) 不吃 —— 合成一张共享清单就会把内层命令当成选项值吞掉.
    value_flags: tuple[str, ...] = ()


# 剥掉之后不改变"真正执行什么"的前缀.
_STRIPPABLE: dict[str, StripRule] = {
    "env": StripRule("assignments"),
    "command": StripRule("flags"),
    "builtin": StripRule("flags"),
    "exec": StripRule("flags", value_flags=("-a",)),
    "nohup": StripRule("none"),
    "stdbuf": StripRule(
        "flags", value_flags=("-i", "-o", "-e", "--input", "--output", "--error")
    ),
    "nice": StripRule("flags", value_flags=("-n", "--adjustment")),
    "ionice": StripRule("flags", value_flags=("-c", "-n", "--class", "--classdata")),
    "time": StripRule("none"),
    "timeout": StripRule("value", value_flags=("-s", "--signal", "-k", "--kill-after")),
    "chrt": StripRule("flags", value_flags=("-p",)),
    "setsid": StripRule("none"),
}

# 提权入口. 永远不剥离: 它们是 Hard Deny 的判据本身 (ADR-0013 §4).
PRIVILEGE_ESCALATORS: frozenset[str] = frozenset(
    {
        "sudo",
        "sudoedit",
        "doas",
        "su",
        "pkexec",
        # systemd 256 起的 sudo 替代品. 漏掉它等于漏掉一整条提权路径.
        "run0",
        "runas",
        "gsudo",
        "Start-Process",
    }
)

# 间接执行: 外层授权不传导给内层.
INDIRECT_EXECUTORS: frozenset[str] = frozenset(
    {"xargs", "find", "eval", "watch", "parallel", "entr", "Invoke-Expression"}
)

# 会执行任意项目代码的入口命令. 不是白名单, 是"这些一定算脚本执行"的清单.
_SCRIPT_ENTRYPOINTS: dict[str, str] = {
    "pytest": "python",
    "tox": "python",
    "nox": "python",
    "poetry": "python",
    "uv": "python",
    "pip": "python",
    "npm": "javascript",
    "npx": "javascript",
    "yarn": "javascript",
    "pnpm": "javascript",
    "make": "make",
    "cargo": "rust",
    "go": "go",
    "mvn": "java",
    "gradle": "java",
    "rake": "ruby",
    "bundle": "ruby",
    "composer": "php",
    "just": "shell",
}

# 脚本文件后缀 -> 语言.
_SCRIPT_SUFFIXES: dict[str, str] = {
    ".py": "python",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".rb": "ruby",
    ".pl": "perl",
    ".php": "php",
    ".ps1": "powershell",
    ".bat": "cmd",
    ".cmd": "cmd",
}


@dataclass(frozen=True)
class NestedCommand:
    """一层包装器里裹着的下一层命令, 需要按对应方言递归解析."""

    shell_kind: ShellKind
    source: str
    encoded: bool = False


def interpreter_of(executable: str) -> Interpreter | None:
    """按查表名找解释器. 大小写不敏感 —— cmd 与 PowerShell 本身就不区分大小写."""
    name = normalize_executable(executable)
    found = _INTERPRETERS.get(name)
    if found is not None:
        return found
    return _INTERPRETERS.get(name.lower())


def strip_prefix(
    executable: str, argv: tuple[str, ...]
) -> tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]] | None:
    """剥掉一层可剥离前缀, 返回内层 (executable, argv, 环境赋值).

    无法剥离 (不是前缀, 或剥完没有内层) 时返回 None. 提权命令永远返回 None.
    """
    name = normalize_executable(executable)
    if name in PRIVILEGE_ESCALATORS or name not in _STRIPPABLE:
        return None
    rule = _STRIPPABLE[name]
    rest = list(argv)
    assignments: list[tuple[str, str]] = []

    if rule.mode == "assignments":
        consumed = _strip_env_flags(rest)
        if consumed is None:
            # `env -S 'python -c ...'` 会把字符串重新切分成命令. 剥掉它等于把内层
            # 藏起来, 所以不剥 —— 让 env 作为未知命令留在单元列表里.
            return None
        while rest and "=" in rest[0] and not rest[0].startswith("-"):
            key, _, value = rest[0].partition("=")
            assignments.append((key, value))
            rest.pop(0)
    elif rule.mode == "value":
        _strip_flags(rest, rule.value_flags)
        if rest:
            rest.pop(0)  # 超时时长本身
    elif rule.mode == "flags":
        _strip_flags(rest, rule.value_flags)

    if not rest:
        return None
    return rest[0], tuple(rest[1:]), tuple(assignments)


def _strip_flags(rest: list[str], value_flags: tuple[str, ...]) -> None:
    """吃掉前导选项. 只有明确会带值的选项才多吃一个参数."""
    while rest and rest[0].startswith("-") and rest[0] != "--":
        flag = rest.pop(0)
        base = flag.split("=", 1)[0]
        if base in value_flags and "=" not in flag and rest:
            rest.pop(0)
    if rest and rest[0] == "--":
        rest.pop(0)


def _strip_env_flags(rest: list[str]) -> bool | None:
    """处理 `env` 自己的选项. 遇到 `-S` 返回 None 表示"不要剥这一层"."""
    while rest and rest[0].startswith("-") and rest[0] != "--":
        flag = rest[0]
        base = flag.split("=", 1)[0]
        if base in ("-S", "--split-string"):
            return None
        rest.pop(0)
        if base in ("-u", "--unset") and "=" not in flag and rest:
            rest.pop(0)
    if rest and rest[0] == "--":
        rest.pop(0)
    return True


def inline_code_of(
    executable: str, argv: tuple[str, ...]
) -> tuple[str, str, bool] | None:
    """取出 `-c` / `-e` 一类内联代码, 返回 (语言, 源码, 是否 base64 编码)."""
    interpreter = interpreter_of(executable)
    if interpreter is None:
        return None
    found = _find_inline(interpreter, argv)
    if found is None:
        return None
    source, encoded = found
    return interpreter.language, source, encoded


def _find_inline(
    interpreter: Interpreter, argv: tuple[str, ...]
) -> tuple[str, bool] | None:
    if interpreter.style is FlagStyle.POSIX:
        return _posix_inline(interpreter, argv)
    if interpreter.style is FlagStyle.GNU:
        return _gnu_inline(interpreter, argv)
    if interpreter.style is FlagStyle.POWERSHELL:
        return _powershell_inline(interpreter, argv)
    return _cmd_inline(interpreter, argv)


def _posix_inline(
    interpreter: Interpreter, argv: tuple[str, ...]
) -> tuple[str, bool] | None:
    """短选项聚合: `bash -lc CODE`, `python -Bc CODE`, `perl -we CODE`."""
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--":
            return None
        if arg.startswith("--"):
            hit = _long_flag(interpreter, argv, index)
            if hit is not None:
                return hit
            index += 1
            continue
        if not arg.startswith("-") or arg == "-":
            # 位置参数出现了: 这是脚本文件名, 后面不会再有内联代码.
            return None
        cluster = arg[1:]
        for position, char in enumerate(cluster):
            rest = cluster[position + 1 :]
            if char in interpreter.inline_flags:
                if rest:
                    return rest, False
                if index + 1 < len(argv):
                    return argv[index + 1], False
                return None
            if char in interpreter.value_taking:
                # 这个字符吃掉一个值: 值是簇里剩下的部分, 否则是下一个参数.
                if not rest:
                    index += 1
                break
        index += 1
    return None


def _long_flag(
    interpreter: Interpreter, argv: tuple[str, ...], index: int
) -> tuple[str, bool] | None:
    name, separator, attached = argv[index].partition("=")
    flag = name.lstrip("-")
    if flag not in interpreter.inline_flags:
        return None
    encoded = flag in interpreter.encoded_flags
    if separator:
        return attached, encoded
    if index + 1 < len(argv):
        return argv[index + 1], encoded
    return None


def _gnu_inline(
    interpreter: Interpreter, argv: tuple[str, ...]
) -> tuple[str, bool] | None:
    """长选项带 `=`: `node --eval=CODE`, 以及 `deno eval CODE` 这类子命令形式."""
    for index, arg in enumerate(argv):
        if arg == "--":
            return None
        if arg in interpreter.inline_subcommands and index + 1 < len(argv):
            return argv[index + 1], False
        if not arg.startswith("-"):
            continue
        hit = _long_flag(interpreter, argv, index)
        if hit is not None:
            return hit
    return None


def _powershell_inline(
    interpreter: Interpreter, argv: tuple[str, ...]
) -> tuple[str, bool] | None:
    """PowerShell 允许唯一前缀缩写且大小写不敏感, 值可用 `:` 紧跟."""
    for index, arg in enumerate(argv):
        if not arg.startswith("-"):
            continue
        typed, separator, attached = arg[1:].partition(":")
        matched = _powershell_flag(interpreter, typed)
        if matched is None:
            continue
        encoded = matched in interpreter.encoded_flags
        if separator:
            return attached, encoded
        if index + 1 < len(argv):
            return argv[index + 1], encoded
    return None


def _powershell_flag(interpreter: Interpreter, typed: str) -> str | None:
    if not typed:
        return None
    lowered = typed.lower()
    exact = [flag for flag in interpreter.inline_flags if flag.lower() == lowered]
    if exact:
        return exact[0]
    hits = [
        flag for flag in interpreter.inline_flags if flag.lower().startswith(lowered)
    ]
    # 有歧义时也按内联代码处理: PowerShell 自己会因为歧义报错, 而把它当代码分析
    # 只会多一次审查, 猜"不是代码"才会漏.
    return hits[0] if hits else None


def _cmd_inline(
    interpreter: Interpreter, argv: tuple[str, ...]
) -> tuple[str, bool] | None:
    """`cmd /c` 之后的**全部**参数都是命令行, 不只是下一个 token."""
    allowed = {flag.lower() for flag in interpreter.inline_flags}
    for index, arg in enumerate(argv):
        if not arg.startswith("/"):
            continue
        if arg[1:].lower() in allowed and index + 1 < len(argv):
            return " ".join(argv[index + 1 :]), False
    return None


def nested_command_of(executable: str, argv: tuple[str, ...]) -> NestedCommand | None:
    """识别 `sh -c` / `cmd /c` / `powershell -Command` 这类包装, 给出内层与方言.

    只覆盖**会再启动一层命令解释**的情形. `python -c` 不在这里: 它的内容是 Python 代码,
    交给 ScriptAnalyzer, 不能按 Shell 语法再解析一遍.
    """
    inline = inline_code_of(executable, argv)
    if inline is None:
        return None
    language, source, encoded = inline
    if language == "shell":
        return NestedCommand(shell_kind=ShellKind.POSIX, source=source)
    if language == "cmd":
        return NestedCommand(shell_kind=ShellKind.CMD, source=source)
    if language == "powershell":
        return NestedCommand(
            shell_kind=ShellKind.POWERSHELL, source=source, encoded=encoded
        )
    return None


def script_entry_of(
    executable: str, argv: tuple[str, ...], *, heredoc: str | None = None
) -> ScriptPayload | None:
    """识别脚本执行 (ADR-0013 §7).

    覆盖四种形态: heredoc 正文, `-c` 内联代码, `解释器 脚本文件`, 以及会跑项目代码的
    入口命令 (pytest / npm test / make ...).
    """
    interpreter = interpreter_of(executable)
    language = interpreter.language if interpreter is not None else None
    if heredoc is not None:
        return ScriptPayload(
            language=language or "unknown", source=heredoc, origin="heredoc"
        )

    inline = inline_code_of(executable, argv)
    if inline is not None:
        inline_language, source, encoded = inline
        if inline_language in ("shell", "cmd"):
            # Shell 内联代码由 Shell 解析器递归处理, 不重复标成脚本.
            return None
        return ScriptPayload(
            language=inline_language,
            source=source,
            origin="encoded" if encoded else "inline",
        )

    if interpreter is not None:
        return _interpreter_script(interpreter, argv)

    name = normalize_executable(executable)
    entry_language = _SCRIPT_ENTRYPOINTS.get(name)
    if entry_language is not None:
        return ScriptPayload(
            language=entry_language,
            path=" ".join((executable, *argv)),
            origin="file",
        )

    suffix_language = _language_of_suffix(name)
    if suffix_language is not None:
        return ScriptPayload(language=suffix_language, path=executable, origin="file")
    return None


def _interpreter_script(
    interpreter: Interpreter, argv: tuple[str, ...]
) -> ScriptPayload | None:
    """`python foo.py` 这类"解释器 + 脚本文件"形态.

    位置参数要跳过选项**和选项的值**才能取到: 早先直接取第一个不以 `-` 开头的参数,
    于是 `python -m pytest` 记成了一个叫 pytest 的脚本文件.
    """
    target = _first_positional(interpreter, argv)
    if target is None:
        if not argv:
            return None  # 裸解释器 (REPL), 没有要跑的东西.
        # 选项把参数吃光了, 但仍然会执行代码: `python -m pytest` 就是这种形状.
        # 认不出跑的是什么, 不等于没跑 —— 交给脚本分析按内容不完整走 ASK.
        return ScriptPayload(language=interpreter.language, origin="unresolved")
    if any(char.isspace() for char in target):
        # 取到的不像文件名而像一段代码 (内联匹配没覆盖到的写法). 不能当文件去读, 也
        # 不能当无事发生: 给一份既无 source 也无 path 的 payload, 让脚本分析按
        # "内容收集不完整"走 ASK.
        return ScriptPayload(language=interpreter.language, origin="unresolved")
    return ScriptPayload(language=interpreter.language, path=target, origin="file")


def _first_positional(interpreter: Interpreter, argv: tuple[str, ...]) -> str | None:
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--":
            return argv[index + 1] if index + 1 < len(argv) else None
        if not arg.startswith("-") or arg == "-":
            return arg
        if arg.startswith("--"):
            if "=" not in arg:
                index += 1  # 保守起见认为长选项带一个值
            index += 1
            continue
        cluster = arg[1:]
        for position, char in enumerate(cluster):
            if char in interpreter.value_taking and not cluster[position + 1 :]:
                index += 1
                break
        index += 1
    return None


def _language_of_suffix(name: str) -> str | None:
    lowered = name.lower()
    for suffix, language in _SCRIPT_SUFFIXES.items():
        if lowered.endswith(suffix):
            return language
    return None
