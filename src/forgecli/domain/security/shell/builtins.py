"""各方言的 Shell 内建命令 (ADR-0013 §3 的补充).

## 为什么需要它

可执行文件解析走的是**受控 PATH 查文件**. 内建命令不是文件 —— `export`, `set`, `dir`
由 Shell 自己解释, PATH 上根本没有对应的东西. 不认识内建命令的后果是把它们全判成
"找不到可执行文件", 于是 `export FOO=1` 这种最普通的操作要么被打断要么被拒.

反过来也成立: `ls` 在 Windows 上既不是 cmd 内建, 也不在 PATH 上 —— 那才是真的跑不了.
把这两种情况分开, 正是这张表存在的唯一理由.

## 一个不在这里解决的细节

某些内建同时存在同名可执行文件 (macOS 有 `/bin/echo`, `/usr/bin/cd`). Shell 优先用内建,
所以把身份绑到那个文件上其实绑错了对象. 影响有限 (内建命令本来就不产生学习规则), 但
不要因为"文件存在"就以为解析结果代表真正会跑的东西.

## PowerShell 为什么是空集

PowerShell 的命令是 cmdlet, 数量上千且可由模块动态注册, 枚举不出封闭集合. 因此这张表
对 PowerShell 只能返回空 —— 调用方必须据此**放弃判定**, 而不是把"不在表里"当成"跑不了".
"""

from __future__ import annotations

from forgecli.domain.security.shell.command_plan import ShellKind

__all__ = ["dialect_has_closed_builtin_set", "is_builtin"]

# POSIX 特殊内建 + 常用内建. 覆盖 sh / bash / zsh 的公共部分.
_POSIX: frozenset[str] = frozenset(
    {
        # POSIX 特殊内建
        "break",
        "colon",
        ":",
        "continue",
        ".",
        "source",
        "eval",
        "exec",
        "exit",
        "export",
        "readonly",
        "return",
        "set",
        "shift",
        "times",
        "trap",
        "unset",
        # 常规内建
        "alias",
        "bg",
        "bind",
        "builtin",
        "cd",
        "command",
        "compgen",
        "complete",
        "declare",
        "dirs",
        "disown",
        "echo",
        "enable",
        "fc",
        "fg",
        "getopts",
        "hash",
        "help",
        "history",
        "jobs",
        "kill",
        "let",
        "local",
        "logout",
        "mapfile",
        "popd",
        "printf",
        "pushd",
        "pwd",
        "read",
        "readarray",
        "shopt",
        "suspend",
        "test",
        "[",
        "type",
        "typeset",
        "ulimit",
        "umask",
        "unalias",
        "wait",
        # 常被当成内建的真值命令: 有些系统有文件, 有些没有
        "true",
        "false",
    }
)

# cmd.exe 内建. 这些在 Windows 上没有对应的 .exe.
_CMD: frozenset[str] = frozenset(
    {
        "assoc",
        "break",
        "call",
        "cd",
        "chdir",
        "cls",
        "color",
        "copy",
        "date",
        "del",
        "dir",
        "echo",
        "endlocal",
        "erase",
        "exit",
        "for",
        "ftype",
        "goto",
        "if",
        "md",
        "mkdir",
        "mklink",
        "move",
        "path",
        "pause",
        "popd",
        "prompt",
        "pushd",
        "rd",
        "rem",
        "ren",
        "rename",
        "rmdir",
        "set",
        "setlocal",
        "shift",
        "start",
        "time",
        "title",
        "type",
        "ver",
        "verify",
        "vol",
    }
)

_BY_DIALECT: dict[ShellKind, frozenset[str]] = {
    ShellKind.POSIX: _POSIX,
    ShellKind.CMD: _CMD,
}


def is_builtin(token: str, dialect: ShellKind) -> bool:
    """这个词是不是该方言的内建命令.

    带路径分隔符的一律不是: `./cd` 与 `/usr/bin/cd` 指的是文件, 不是内建.
    """
    if not token or "/" in token or "\\" in token:
        return False
    table = _BY_DIALECT.get(dialect)
    if table is None:
        return False
    # cmd 不区分大小写; POSIX 区分.
    return (token.lower() if dialect is ShellKind.CMD else token) in table


def dialect_has_closed_builtin_set(dialect: ShellKind) -> bool:
    """这个方言的内建集合是否可枚举.

    只有可枚举时, "既不是内建也不在 PATH 上"才等于"跑不了". PowerShell 不可枚举,
    对它必须保守处理 —— 判不出来就别判死.
    """
    return dialect in _BY_DIALECT
