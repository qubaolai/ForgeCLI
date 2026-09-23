"""只读命令表与 `is_read_only` (对应 BashTool/readOnlyValidation.ts).

判据是 fail closed 的: 每个子命令都要在表里, 且没带表里列为不安全的参数; 有输出重定向,
有动态执行, 含 `cd` 又含 `git`, 或来自命令替换, 都不算只读. /dev/null 输出除外.
cd 还须通过整条命令的静态控制流与权限层路径检查. 只读的命令在 default 与
plan 下直接放行 (bash_permissions 的第 7 步).
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.security.shell.command_plan import (
    CommandPlan,
    CommandUnit,
    ParseStatus,
    RedirectKind,
    UnitOrigin,
)
from forgecli.tools.builtin.Bash.git_validation import git_query_is_read_only
from forgecli.tools.builtin.Bash.read_paths import read_operands
from forgecli.tools.builtin.Bash.sed_validation import sed_read_paths
from forgecli.tools.builtin.Bash.static_paths import is_null_sink, static_cd_targets

__all__ = ["ReadOnlyVerdict", "check_read_only_constraints", "unit_is_read_only"]


@dataclass(frozen=True)
class ReadOnlyVerdict:
    read_only: bool
    reason: str = ""


@dataclass(frozen=True)
class _Spec:
    # 带这些参数就不算只读 (前缀比: `-exec` 也挡 `-execdir`).
    unsafe_flags: tuple[str, ...] = ()
    # 参数里出现这些子串就不算只读 (awk 的 system(, sed 的 w 命令一类).
    unsafe_fragments: tuple[str, ...] = ()
    # 只有这些子命令 (argv[0]) 才算只读; 空 = 不看子命令.
    subcommands: tuple[str, ...] = ()
    # 子命令之外还要挡的参数.
    subcommand_unsafe_flags: dict[str, tuple[str, ...]] | None = None


_PLAIN = _Spec()

_TABLE: dict[str, _Spec] = {
    # 看文件与目录.
    "ls": _PLAIN,
    "cat": _PLAIN,
    "head": _PLAIN,
    "tail": _Spec(unsafe_flags=("-f", "--follow", "-F")),
    "wc": _PLAIN,
    "stat": _PLAIN,
    "file": _PLAIN,
    "tree": _Spec(unsafe_flags=("-o",)),
    "pwd": _PLAIN,
    "cd": _PLAIN,
    "basename": _PLAIN,
    "dirname": _PLAIN,
    "realpath": _PLAIN,
    "readlink": _PLAIN,
    "du": _PLAIN,
    "df": _PLAIN,
    "nl": _PLAIN,
    "strings": _PLAIN,
    "hexdump": _PLAIN,
    "od": _PLAIN,
    "less": _PLAIN,
    "more": _PLAIN,
    # 系统信息.
    "echo": _PLAIN,
    "printf": _PLAIN,
    "true": _PLAIN,
    "false": _PLAIN,
    "date": _Spec(unsafe_flags=("-s", "--set")),
    "cal": _PLAIN,
    "uptime": _PLAIN,
    "whoami": _PLAIN,
    "id": _PLAIN,
    "uname": _PLAIN,
    "hostname": _Spec(unsafe_flags=("-b", "-F", "--file")),
    "which": _PLAIN,
    "type": _PLAIN,
    "command": _Spec(unsafe_flags=("",)),  # `command foo` 会跑 foo
    "env": _Spec(unsafe_flags=("",)),  # `env foo` 会跑 foo
    "nproc": _PLAIN,
    "free": _PLAIN,
    "locale": _PLAIN,
    "groups": _PLAIN,
    "ps": _PLAIN,
    "test": _PLAIN,
    "[": _PLAIN,
    # 文本处理 (不带能写文件或执行的参数).
    "cut": _PLAIN,
    "tr": _PLAIN,
    "sort": _Spec(unsafe_flags=("-o", "--output")),
    "uniq": _PLAIN,
    "paste": _PLAIN,
    "comm": _PLAIN,
    "diff": _PLAIN,
    "cmp": _PLAIN,
    "column": _PLAIN,
    "fold": _PLAIN,
    "fmt": _PLAIN,
    "rev": _PLAIN,
    "tac": _PLAIN,
    "expand": _PLAIN,
    "unexpand": _PLAIN,
    "md5sum": _PLAIN,
    "sha1sum": _PLAIN,
    "sha256sum": _PLAIN,
    "shasum": _PLAIN,
    "base64": _Spec(unsafe_flags=("-o", "--output")),
    "grep": _Spec(unsafe_flags=("-f", "--file")),
    "egrep": _Spec(unsafe_flags=("-f", "--file")),
    "fgrep": _Spec(unsafe_flags=("-f", "--file")),
    "rg": _Spec(unsafe_flags=("--pre", "-f", "--file", "--files-from")),
    "jq": _Spec(unsafe_flags=("-f", "--from-file"), unsafe_fragments=("input(",)),
    "find": _Spec(
        unsafe_flags=(
            "-exec",
            "-execdir",
            "-ok",
            "-okdir",
            "-delete",
            "-fprint",
            "-fprint0",
            "-fprintf",
            "-fls",
            "-files0-from",
        )
    ),
    "awk": _Spec(unsafe_fragments=("system(", "|", ">", "getline")),
    "gawk": _Spec(unsafe_fragments=("system(", "|", ">", "getline")),
    # 包管理与工具链的只读子命令.
    "npm": _Spec(
        subcommands=(
            "ls",
            "list",
            "view",
            "info",
            "show",
            "outdated",
            "why",
            "explain",
            "help",
            "version",
            "--version",
            "-v",
        )
    ),
    "pnpm": _Spec(
        subcommands=("ls", "list", "why", "outdated", "help", "--version", "-v")
    ),
    "yarn": _Spec(subcommands=("list", "why", "info", "help", "--version", "-v")),
    "pip": _Spec(subcommands=("list", "show", "freeze", "check", "help", "--version")),
    "pip3": _Spec(subcommands=("list", "show", "freeze", "check", "help", "--version")),
    "poetry": _Spec(subcommands=("show", "check", "env", "help", "--version")),
    "cargo": _Spec(subcommands=("tree", "metadata", "--version", "help")),
    "go": _Spec(subcommands=("version", "env", "list", "help")),
    "python": _Spec(subcommands=("--version", "-V")),
    "python3": _Spec(subcommands=("--version", "-V")),
    "node": _Spec(subcommands=("--version", "-v")),
    "docker": _Spec(subcommands=("ps", "images", "version", "info", "inspect", "logs")),
    "kubectl": _Spec(subcommands=("get", "describe", "version", "config", "logs")),
}


def unit_is_read_only(unit: CommandUnit) -> str:
    """空串 = 只读, 否则是不只读的理由."""
    if unit.origin in (UnitOrigin.SUBSTITUTION, UnitOrigin.PROCESS_SUBSTITUTION):
        return "command substitution"
    if unit.dynamic_execution:
        return f"{unit.name} executes dynamic content"
    if any(not is_null_sink(target) for target in unit.write_targets):
        return "output redirection"
    for redirect in unit.redirects:
        if redirect.kind is RedirectKind.DUPLICATE and not (
            redirect.target.isascii()
            and redirect.target.isdigit()
            or redirect.target == "-"
        ):
            return "non-descriptor redirection"
    if unit.name == "git":
        return (
            ""
            if git_query_is_read_only(unit.argv)
            else "git invocation is not a proven query"
        )
    if unit.name == "uniq" and len(read_operands(unit)) > 1:
        return "uniq output file is not read-only"
    if unit.name == "sed":
        return (
            ""
            if sed_read_paths(unit) is not None
            else "sed script is not a proven read-only query"
        )
    if unit.name == "npm" and unit.argv[:1] == ("version",) and len(unit.argv) != 1:
        return "npm version with arguments can modify files"
    if (
        unit.name == "poetry"
        and unit.argv[:1] == ("env",)
        and unit.argv[1:2] not in (("info",), ("list",))
    ):
        return "poetry env operation is not a query"
    if (
        unit.name == "go"
        and unit.argv[:1] == ("env",)
        and any(a in ("-w", "-u") for a in unit.argv)
    ):
        return "go env modifies configuration"
    if (
        unit.name == "kubectl"
        and unit.argv[:1] == ("config",)
        and unit.argv[1:2]
        not in (
            ("view",),
            ("current-context",),
            ("get-contexts",),
            ("get-clusters",),
            ("get-users",),
        )
    ):
        return "kubectl config operation is not a query"
    spec = _TABLE.get(unit.name)
    if spec is None:
        return f"{unit.name} is not a known read-only command"
    argv = unit.argv
    if spec.subcommands:
        if not argv or argv[0] not in spec.subcommands:
            return f"{unit.name} {argv[0] if argv else ''} is not read-only".strip()
        extra: tuple[str, ...] = ()
        if spec.subcommand_unsafe_flags is not None:
            extra = spec.subcommand_unsafe_flags.get(argv[0], ())
        for arg in argv[1:]:
            for flag in extra:
                if arg == flag or (flag.startswith("-") and arg.startswith(flag + "=")):
                    return f"{unit.name} {argv[0]} {arg} modifies state"
        return ""
    for arg in argv:
        for flag in spec.unsafe_flags:
            if flag == "":
                if not arg.startswith("-") and "=" not in arg:
                    return f"{unit.name} would run {arg}"
                continue
            if _flag_hit(arg, flag):
                return f"{unit.name} {arg} is not read-only"
        for fragment in spec.unsafe_fragments:
            if fragment in arg:
                return f"{unit.name} argument contains {fragment!r}"
    return ""


def _flag_hit(arg: str, flag: str) -> bool:
    """`-f` 也挡 `-rf` 这种合并写法; 长参数认 `--x` 与 `--x=`."""
    if arg == flag or arg.startswith(flag + "="):
        return True
    short = len(flag) == 2 and flag.startswith("-") and not flag.startswith("--")
    if short and arg.startswith("-") and not arg.startswith("--"):
        return flag[1] in arg[1:]
    return False


def check_read_only_constraints(plan: CommandPlan) -> ReadOnlyVerdict:
    if plan.status is not ParseStatus.OK:
        return ReadOnlyVerdict(False, "command could not be fully parsed")
    if not plan.units:
        return ReadOnlyVerdict(False, "empty command")
    if static_cd_targets(plan) is None:
        return ReadOnlyVerdict(False, "directory changes cannot be statically verified")
    names = [unit.name for unit in plan.units]
    if "cd" in names and "git" in names:
        return ReadOnlyVerdict(False, "compound command with cd and git")
    for unit in plan.units:
        reason = unit_is_read_only(unit)
        if reason:
            return ReadOnlyVerdict(False, reason)
    return ReadOnlyVerdict(True)
