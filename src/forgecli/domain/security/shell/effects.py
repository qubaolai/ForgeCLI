"""一条命令对文件系统做什么: 命令名 -> 影响形态 (ADR-0013 §6.2).

这个模块存在的理由是**默认值的方向**. 早先这份知识散在分析器里, 形状是"写清单里的算
写, 删清单里的算删, 其余按只读处理". 最后那一句是一句没有依据的断言: `java -jar x.jar`
不在任何清单里, 于是被判成"只读了 x.jar"; `sed -i`, `chmod 777`, `tar -x` 同理. 判错
的后果不只是少一项能力 —— 恢复层看 `mutates_workspace` 决定要不要建恢复点, 于是这些
命令连恢复点都没有.

所以这里的默认值是 `UNPROVEN`: **不认识就是不知道它碰什么**, 由调用方按"无法证明"处理
(目标集合不封闭 + 需要人类确认), 而不是按"什么都没碰"处理.

清单仍然是清单, 漏项仍然会发生. 区别在于漏一项的代价: 从"静默放行"变成"多问一次".

只读判定还有一层麻烦: 很多命令**取决于参数**. `sed` 不写, `sed -i` 写; `sort` 不写,
`sort -o f` 写. 这类写在 _CONDITIONAL_WRITERS 里, 按参数判定, 而不是把整个命令归到
某一边.
"""

from __future__ import annotations

from enum import Enum

from forgecli.domain.security.shell.command_plan import CommandUnit

__all__ = ["EffectKind", "effect_kind_of", "runs_arbitrary_code"]


class EffectKind(Enum):
    """这个单元的位置参数意味着什么."""

    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    MOVE = "move"
    # 会跑任意项目代码 (解释器, 测试框架, 构建工具). 位置参数按读取记, 但影响范围要靠
    # 脚本分析与分类器判, 不靠这里.
    RUNS_CODE = "runs_code"
    # 不认识. 影响范围无法推导.
    UNPROVEN = "unproven"


_DELETING = frozenset({"rm", "rmdir", "unlink", "del", "erase", "Remove-Item"})
_MOVING = frozenset({"mv", "rename", "move", "Move-Item", "Rename-Item"})

_WRITING = frozenset(
    {
        "cp",
        "copy",
        "install",
        "tee",
        "touch",
        "mkdir",
        "truncate",
        "dd",
        "patch",
        "Copy-Item",
        "New-Item",
        "Set-Content",
        "Add-Content",
        "Out-File",
        # 元数据写入: 不改内容但改权限与归属, 对安全同样是写.
        "chmod",
        "chown",
        "chgrp",
        "chflags",
        "xattr",
        "setfacl",
        "ln",
        "link",
        "mkfifo",
        "mknod",
        # 归档与压缩: 解包写一批文件, 打包写归档本身. 按参数区分收益不大, 一律算写.
        "tar",
        "unzip",
        "zip",
        "gzip",
        "gunzip",
        "bzip2",
        "bunzip2",
        "xz",
        "unxz",
        "zstd",
        "7z",
        "7za",
        "Expand-Archive",
        "Compress-Archive",
    }
)

# 取决于参数才写的命令: 命令名 -> 使它写入的选项.
#
# 短选项按**字符**匹配, 因为聚合写法同样成立: `sed -ni` 与 `sed -i -n` 都会就地改写.
_CONDITIONAL_WRITERS: dict[str, tuple[str, ...]] = {
    "sed": ("i", "--in-place"),
    "gsed": ("i", "--in-place"),
    "perl": ("i",),
    "ruby": ("i",),
    "sort": ("o", "--output"),
    "awk": ("--in-place",),
    "gawk": ("i", "--in-place"),
    "split": (),  # 无条件写, 但目标名由前缀决定; 放这里只为记录它会写.
    "csplit": (),
}

# 影响范围可推导且确定只读的命令. 位置参数按读取记.
#
# 这不是"安全命令白名单": 它只声明"这些命令的位置参数是读取目标". 允不允许读某个路径
# 仍然由受保护路径检查与模式预算裁决.
_KNOWN_READERS = frozenset(
    {
        "ls",
        "dir",
        "cat",
        "bat",
        "type",
        "head",
        "tail",
        "wc",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "ack",
        "ag",
        "diff",
        "cmp",
        "comm",
        "sort",
        "uniq",
        "cut",
        "tr",
        "column",
        "fold",
        "rev",
        "nl",
        "od",
        "xxd",
        "strings",
        "file",
        "stat",
        "du",
        "df",
        "tree",
        "basename",
        "dirname",
        "realpath",
        "readlink",
        "pwd",
        "echo",
        "printf",
        "true",
        "false",
        "test",
        "date",
        "seq",
        "yes",
        "which",
        "whereis",
        "command",
        "hostname",
        "uname",
        "id",
        "whoami",
        "groups",
        "ps",
        "top",
        "uptime",
        "env",
        "printenv",
        "locale",
        "md5sum",
        "sha1sum",
        "sha256sum",
        "shasum",
        "cksum",
        "jq",
        "yq",
        "tac",
        "expr",
        "sleep",
        "Get-Content",
        "Get-ChildItem",
        "Get-Item",
        "Select-String",
        "Measure-Object",
    }
)

# 会跑任意项目代码的入口. 与 wrappers._SCRIPT_ENTRYPOINTS 是同一类事实, 但这里要的是
# "位置参数怎么记", 所以单独列一份可读的名字集合.
_RUNS_CODE = frozenset(
    {
        "sh",
        "bash",
        "zsh",
        "dash",
        "ksh",
        "fish",
        "python",
        "python2",
        "python3",
        "node",
        "deno",
        "bun",
        "ruby",
        "perl",
        "php",
        "pwsh",
        "powershell",
        "cmd",
        "osascript",
        "java",
        "javac",
        "dotnet",
        "mono",
        "go",
        "cargo",
        "rustc",
        "make",
        "cmake",
        "ninja",
        "gradle",
        "mvn",
        "ant",
        "pytest",
        "tox",
        "nox",
        "poetry",
        "uv",
        "pip",
        "pipx",
        "npm",
        "npx",
        "yarn",
        "pnpm",
        "bundle",
        "rake",
        "composer",
        "just",
        "task",
        "docker",
        "docker-compose",
        "podman",
        "kubectl",
        "helm",
        "terraform",
        "ansible",
        "vagrant",
        "gcc",
        "clang",
        "g++",
        "clang++",
        "swift",
        "swiftc",
        "kotlin",
        "kotlinc",
        "scala",
        "groovy",
        "elixir",
        "mix",
        "erl",
        "lua",
        "julia",
        "Rscript",
        "ts-node",
        "tsx",
        "vite",
        "webpack",
        "esbuild",
        "tsc",
        "sqlite3",
        "psql",
        "mysql",
        "redis-cli",
        "gdb",
        "lldb",
        "strace",
        "dtrace",
        "vim",
        "vi",
        "nvim",
        "emacs",
        "ed",
        "less",
        "man",
        "gpg",
        "openssl",
    }
)

# git 两面都是: `git status` 只读, `git commit` 写. 复用只读子命令这份既有知识,
# 而不是把整个 git 归到某一边.
_GIT_READ_ONLY_SUBCOMMANDS = frozenset(
    {
        "status",
        "diff",
        "log",
        "show",
        "blame",
        "branch",
        "remote",
        "stash",
        "ls-files",
        "ls-tree",
        "rev-parse",
        "describe",
        "shortlog",
        "cat-file",
        "config",
        "grep",
        "reflog",
        "whatchanged",
        "verify-commit",
    }
)
_GIT_LIKE = frozenset({"git", "hg", "svn", "jj"})


def effect_kind_of(unit: CommandUnit) -> EffectKind:
    """这个单元的位置参数属于哪一类. 认不出来一律 UNPROVEN."""
    name = unit.name
    if name in _DELETING:
        return EffectKind.DELETE
    if name in _MOVING:
        return EffectKind.MOVE
    if name in _WRITING:
        return EffectKind.WRITE
    if name in _CONDITIONAL_WRITERS and _writes_in_place(name, unit.argv):
        return EffectKind.WRITE
    if name in _GIT_LIKE:
        return EffectKind.READ if _git_is_read_only(unit.argv) else EffectKind.WRITE
    if name in _RUNS_CODE:
        return EffectKind.RUNS_CODE
    if name in _KNOWN_READERS or name in _CONDITIONAL_WRITERS:
        return EffectKind.READ
    return EffectKind.UNPROVEN


def runs_arbitrary_code(unit: CommandUnit) -> bool:
    """这个单元是否会执行任意代码. 脚本分析与分类器据此决定要不要接手."""
    return effect_kind_of(unit) is EffectKind.RUNS_CODE


def _writes_in_place(name: str, argv: tuple[str, ...]) -> bool:
    """按参数判断"这次"写不写. 短选项按字符看, 聚合写法 (`sed -ni`) 同样命中."""
    triggers = _CONDITIONAL_WRITERS[name]
    if not triggers:
        return True
    long_flags = {flag for flag in triggers if flag.startswith("--")}
    short_chars = {flag for flag in triggers if not flag.startswith("--")}
    for arg in argv:
        if arg == "--":
            break
        if arg.startswith("--"):
            if arg.split("=", 1)[0] in long_flags:
                return True
            continue
        if arg.startswith("-") and any(char in arg[1:] for char in short_chars):
            return True
    return False


def _git_is_read_only(argv: tuple[str, ...]) -> bool:
    """第一个位置参数就是子命令. 取不到子命令时按写处理 —— 裸 `git` 不该被当成只读."""
    for arg in argv:
        if arg.startswith("-"):
            continue
        return arg in _GIT_READ_ONLY_SUBCOMMANDS
    return False
