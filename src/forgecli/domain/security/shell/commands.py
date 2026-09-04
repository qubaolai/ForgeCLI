"""一条命令的全部已知事实: 影响形态 + 参数结构 + 能否证明只读 (ADR-0013 §6.2, ADR-0028).

**一条命令一条记录.** ADR-0028 之前这份知识切成三份 —— `effects.py` 说"它是读还是写",
`arguments.py` 说"它的哪几个参数是路径", `wrappers.py` 说"它是不是解释器" —— 键集大量
重叠, 加一条命令要改三处, 而漏一处不会报错.

## 两个默认值, 方向相反, 都要对

`effect` 默认 `UNPROVEN`: **不认识就是不知道它碰什么**. 早先的默认是"其余按只读处理",
那是一句没有依据的断言 —— `java -jar x.jar` 不在任何清单里, 于是被判成"只读了 x.jar";
`sed -i`, `chmod 777`, `tar -x` 同理. 判错的代价不只是少一项能力: 恢复层看
`mutates_workspace` 决定要不要建恢复点, 于是这些命令连恢复点都没有.

本表**整张都是记账方向的**, 没有放行方向的字段 (ADR-0030 决策 6). 原先有一个
`proven_read_only` 标记 29 条命令名进 ADR-0024 的快速放行, 那是唯一一处"进表等于少一道
人类确认"的地方 —— 它随 ADR-0024 一起作废: 放行现在由围栏决定, 不由命令名决定.

于是这张表的错法只剩一种: 漏一条 = 审批清单少一项, 或者快照多打一次. **不会少问一次.**
这正是 ADR-0030 决策 1 那条判定规则要的形状.

## 参数结构

`arguments` 错了会**凭空造出目标**. 在它出现之前判据只有 `not arg.startswith("-")`:

    grep -r skipUrl .          -> 读取 /ws/skipUrl      搜索词被当成文件
    sed -i.bak s/a/b/ f.txt    -> 写入 /ws/s/a/b        sed 表达式被当成要写的文件
    tar -czf out.tgz src       -> 写入 /ws/src          tar 只读 src, 这里说它要写

这些假路径会一路走到审批框, 与真实目标混排成一份"读取 (6 项)". 用户学会忽略那份清单
之后, 真正危险的那一条也就跟着被忽略了 —— **一份掺假的清单比没有清单更糟**.

**这不是安全白名单.** 表里有没有一条命令, 只影响它的参数怎么解读与要不要多问一次;
允不允许碰某个路径仍然由受保护路径检查, 模式预算与目标封闭性裁决.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "GIT_LIKE",
    "GIT_READ_ONLY_SUBCOMMANDS",
    "NETWORK_TOOLS",
    "ArgumentModel",
    "CommandFacts",
    "EffectKind",
    "facts_for",
]


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


@dataclass(frozen=True)
class ArgumentModel:
    """一条命令怎么消费它的参数.

    短选项按**字符**登记 (`f`), 长选项带 `--` 登记 (`--file`). 短选项要按字符是因为
    聚合写法同样成立: `tar -czf out.tgz` 里吃掉 `out.tgz` 的是 `f`.
    """

    # 吃掉后一个参数, 而那个参数**不是**路径: `find -name '*.tmp'`, `grep -m 5`.
    value_options: frozenset[str] = frozenset()
    # 吃掉后一个参数, 而那个参数**是**路径: `tar -f out.tgz`, `sort -o out`.
    path_options: frozenset[str] = frozenset()
    # 前几个位置参数不是路径: `grep` 的第一个是模式, `sed` 的第一个是脚本.
    leading_non_paths: int = 0
    # 上面那几个位置参数被这些选项顶掉了: 给了 `grep -e foo` 之后, 第一个位置参数就是
    # 文件而不是模式. 不看这一条会把真实文件从清单里漏掉, 那是往危险方向错.
    leading_supplied_by: frozenset[str] = frozenset()
    # 第一个选项之后的位置参数不再是路径: `find . -name x -print` 里只有 `.` 是路径,
    # 后面整段是表达式.
    trailing_positionals_are_paths: bool = True
    # 位置参数一个都不是路径: `echo hello`, `sleep 5`.
    positionals_are_paths: bool = True


_NO_PATHS = ArgumentModel(positionals_are_paths=False)


@dataclass(frozen=True)
class CommandFacts:
    """一条命令的记录. 表外命令拿到的是这份默认值 —— 全部朝保守方向."""

    effect: EffectKind = EffectKind.UNPROVEN
    # None = 参数结构未登记, 调用方按「全部位置参数都可能是路径」推测处理.
    # 与 ArgumentModel() 区分开: 后者是一个真实登记过的、恰好全取默认值的模型.
    arguments: ArgumentModel | None = None
    # 给了这些选项才写 (`sed -i`, `sort -o`, `perl -i`). 非空即表示要按参数复判,
    # 命中时 effect 提升为 WRITE. 短选项按字符登记, 因为 `sed -ni` 与 `sed -i -n` 都会
    # 就地改写.
    #
    # 无条件写的命令直接登记 effect=WRITE, 不走这里: 用空元组表达「总是写」会与
    # 「没登记过」撞成同一个值.
    write_flags: tuple[str, ...] = ()


# ADR-0040 B 类表: 一条命令一条记录 (影响形态 + 参数结构). 它**不是**只读命令白名单 ——
# ADR-0030 已经把"证明只读"从授权路径上取下来了.
# 表外默认: 未登记的命令按"可能写"处理 (见 effects.py), 目标集合按 UNPROVEN 处理.
# 漏一项的后果: 多打一次快照, 多问一次人.
_COMMANDS: dict[str, CommandFacts] = {}


def _register(names: tuple[str, ...], facts: CommandFacts) -> None:
    for name in names:
        _COMMANDS[name] = facts


def facts_for(name: str) -> CommandFacts:
    """表外命令返回默认记录: UNPROVEN + 参数结构未知 + 不可快速放行."""
    return _COMMANDS.get(name, CommandFacts())


# ---- 删 / 移 / 写 ----
#
# 这三类是**风险方向**: 漏一条的后果是 effect 落到 UNPROVEN, 于是目标集合不封闭且要
# 问人 —— 清单少一项, 不会放行.

_register(
    ("rm", "rmdir", "unlink", "del", "erase", "Remove-Item"),
    CommandFacts(effect=EffectKind.DELETE),
)
_register(
    ("mv", "rename", "move", "Move-Item", "Rename-Item"),
    CommandFacts(effect=EffectKind.MOVE),
)
_register(
    (
        "cp",
        "copy",
        "install",
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
        "unzip",
        "gzip",
        "gunzip",
        "bzip2",
        "bunzip2",
        "xz",
        "unxz",
        "zstd",
        "Expand-Archive",
        "Compress-Archive",
    ),
    CommandFacts(effect=EffectKind.WRITE),
)
_register(("tee",), CommandFacts(effect=EffectKind.WRITE))
# 归档: -f 的值是归档文件本身, 其余位置参数是被打包/解包的路径.
_register(
    ("tar", "gtar"),
    CommandFacts(
        effect=EffectKind.WRITE,
        arguments=ArgumentModel(
            value_options=frozenset({"--format", "--owner", "--group"}),
            path_options=frozenset(
                {"f", "C", "--file", "--directory", "--exclude", "--exclude-from"}
            ),
        ),
    ),
)
_register(
    ("zip", "7z", "7za"),
    CommandFacts(
        effect=EffectKind.WRITE,
        arguments=ArgumentModel(path_options=frozenset({"d", "--output-dir"})),
    ),
)

# ---- 取决于参数才写 ----

_register(
    ("sed", "gsed"),
    CommandFacts(
        effect=EffectKind.READ,
        write_flags=("i", "--in-place"),
        arguments=ArgumentModel(
            value_options=frozenset({"e", "--expression"}),
            path_options=frozenset({"f", "--file"}),
            leading_non_paths=1,
            leading_supplied_by=frozenset({"e", "--expression", "f", "--file"}),
        ),
    ),
)
_register(
    ("awk", "mawk", "nawk"),
    CommandFacts(
        effect=EffectKind.READ,
        write_flags=("--in-place",),
        arguments=ArgumentModel(
            value_options=frozenset({"v", "--assign"}),
            path_options=frozenset({"f", "--file"}),
            leading_non_paths=1,
            leading_supplied_by=frozenset({"f", "--file"}),
        ),
    ),
)
_register(
    ("gawk",),
    CommandFacts(
        effect=EffectKind.READ,
        write_flags=("i", "--in-place"),
        arguments=ArgumentModel(
            value_options=frozenset({"v", "--assign"}),
            path_options=frozenset({"f", "--file"}),
            leading_non_paths=1,
            leading_supplied_by=frozenset({"f", "--file"}),
        ),
    ),
)
_register(
    ("sort",),
    CommandFacts(
        effect=EffectKind.READ,
        write_flags=("o", "--output"),
        arguments=ArgumentModel(path_options=frozenset({"o", "--output"})),
    ),
)
# split / csplit 无条件写, 只是目标名由前缀决定而推导不出来.
_register(("split", "csplit"), CommandFacts(effect=EffectKind.WRITE))

# ---- 会跑任意项目代码 ----
#
# 不按测试框架穷举: 它们都能执行任意项目代码, 而框架的种类没有尽头. 与
# wrappers._SCRIPT_ENTRYPOINTS 描述同一类事实, 那边回答"内联代码写在哪个选项里",
# 这边回答"位置参数怎么记" —— 两边都在这个包内, 加命令时一起改.

_register(
    (
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
    ),
    CommandFacts(effect=EffectKind.RUNS_CODE),
)
# perl / ruby 的 `-i` 就地改写文件: 基础形态仍是 RUNS_CODE, 给了 -i 才提升为 WRITE.
# 漏掉这一条的后果不是放行, 是审批清单把「写入 f.txt」说成「读取 f.txt」.
_register(
    ("perl", "ruby"),
    CommandFacts(effect=EffectKind.RUNS_CODE, write_flags=("i",)),
)

# xargs 的位置参数是"要跑的命令 + 它的参数", 由 wrappers 提取成独立单元处理.
# 留在这里当路径的话, `xargs rm -rf build` 会记一条名叫 rm 的读取目标.
_register(("xargs",), CommandFacts(effect=EffectKind.RUNS_CODE, arguments=_NO_PATHS))

# ---- 只读核心集 (ADR-0024 快速放行, 放行方向) ----

_register(
    (
        "ls",
        "dir",
        "cat",
        "head",
        "tail",
        "wc",
        "file",
        "stat",
        "du",
        "df",
        "tree",
        "basename",
        "dirname",
        "realpath",
        "pwd",
    ),
    CommandFacts(effect=EffectKind.READ),
)
# grep 家族: 第一个位置参数是模式, 除非 -e / -f 已经给过模式.
_register(
    ("grep", "egrep", "fgrep", "rg", "ripgrep"),
    CommandFacts(
        effect=EffectKind.READ,
        arguments=ArgumentModel(
            value_options=frozenset(
                {
                    "e",
                    "m",
                    "A",
                    "B",
                    "C",
                    "--regexp",
                    "--max-count",
                    "--after-context",
                    "--before-context",
                    "--context",
                    "--include",
                    "--exclude",
                    "--exclude-dir",
                    "--glob",
                    "--type",
                    "--color",
                }
            ),
            path_options=frozenset({"f", "--file"}),
            leading_non_paths=1,
            leading_supplied_by=frozenset({"e", "--regexp", "f", "--file"}),
        ),
    ),
)
# find: 起点目录写在第一个选项之前, 之后整段是表达式.
#
# `-exec` 的内层命令由 wrappers 提取成独立单元 (UnitOrigin.WRAPPER_INNER), 不在这里当
# 参数处理 —— 那条命令有自己的影响形态, 把 `rm` 当成一个路径名是这份清单最离谱的一处.
_register(
    ("find", "fd"),
    CommandFacts(
        effect=EffectKind.READ,
        arguments=ArgumentModel(
            value_options=frozenset(
                {
                    "--name",
                    "--iname",
                    "--path",
                    "--type",
                    "--maxdepth",
                    "--mindepth",
                    "--exec",
                    "--regex",
                    "--perm",
                    "--user",
                    "--group",
                    "--size",
                }
            ),
            trailing_positionals_are_paths=False,
        ),
    ),
)
# 参数与文件系统无关的只读命令: `echo hello` 不该记一条 /ws/hello.
_register(
    ("echo", "printf", "which", "uname", "whoami", "date"),
    CommandFacts(effect=EffectKind.READ, arguments=_NO_PATHS),
)
_register(("jq",), CommandFacts(effect=EffectKind.READ))

# ---- 只读但不进快速放行 ----
#
# 位置参数仍按读取记 (审批清单照常准确), 但不免掉那次人类确认. 要放进核心集是一次
# 显式改动, 而"在 READ 里所以顺带放行"是一条静默的口子.

_register(
    (
        "bat",
        "type",
        "ack",
        "ag",
        "diff",
        "cmp",
        "comm",
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
        "readlink",
        "test",
        "tac",
        "md5sum",
        "sha1sum",
        "sha256sum",
        "shasum",
        "cksum",
        "yq",
        "ps",
        "top",
        "env",
        "Get-Content",
        "Get-ChildItem",
        "Get-Item",
        "Select-String",
        "Measure-Object",
    ),
    CommandFacts(effect=EffectKind.READ),
)
_register(
    (
        "sleep",
        "seq",
        "yes",
        "expr",
        "true",
        "false",
        "hostname",
        "id",
        "groups",
        "uptime",
        "printenv",
        "locale",
        "whereis",
    ),
    CommandFacts(effect=EffectKind.READ, arguments=_NO_PATHS),
)

# ---- git 家族 ----
#
# 两面都是: `git status` 只读, `git commit` 写. 复用只读子命令这份既有知识, 而不是把
# 整个 git 归到某一边. 判定在 effects.effect_kind_of.
#
# 这张表**只服务 shell_run**: 用户或模型手敲 `git ...` 时, 它回答"这个子命令读还是写".
#
# 曾经还有一个 `git_read` 工具, 里面另有一份 CLI 参数白名单要和这里对口径, 靠一条注释
# 绑着 (两处分属 security 与 tools, check_arch.py 禁止互相 import). 那个工具已经删掉,
# git 只读查询回到 shell_run, 于是这里成了全库唯一一份 git 知识 —— 没有第二处要对齐.
# 它是 ADR-0040 说的 B 类非承重表: 表外的子命令走 UNPROVEN/ASK, 漏一项只会多问一次.

# ADR-0040 B 类表: 哪些命令的第一个位置参数是子命令.
# 表外默认: 认不出的 VCS 按普通命令处理, 影响形态由上面的记录表推导, 表外"可能写".
# 漏一项的后果: 多问一次人.
GIT_LIKE: frozenset[str] = frozenset({"git", "hg", "svn", "jj"})

# ADR-0040 B 类表 (见上面那段: 它现在只服务 shell_run).
# 表外默认: 未登记的子命令走 UNPROVEN/ASK.
# 漏一项的后果: 多问一次人.
GIT_READ_ONLY_SUBCOMMANDS: frozenset[str] = frozenset(
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


# ---- 网络工具 ----
#
# 这是 ADR-0040 §8.3 说的 **C 类** 表: 它已经退出授权路径. 命中不再决定要不要打断用户
# (那由围栏决定, 见 domain/security/budget.py 的 _NEEDS_FENCE), 只剩两个用处:
#
# 1. 给人看的风险摘要 —— `PlanEffects.network_targets` 从命中的单元里摘出地址;
# 2. Hard Deny 的 `curl | sh` 形状判定 —— 管线上游是不是在从网上取字节.
#
# 漏一条的后果因此从"少一次审批"降成"风险摘要少一行, 少一条纵深". 表外仍有围栏与
# EXECUTE_SHELL 兜底.
#
# 合成一份是 2026-08-28 做的: 在那之前 `command_plan.py` 自带一份**只有 5 条**的副本
# 供 Hard Deny 用, 于是 `nc`, `socat`, `ssh` 这些在风险摘要里算网络工具, 在
# `xxx | sh` 的红线判定里却不算.
NETWORK_TOOLS: frozenset[str] = frozenset(
    {
        "curl",
        "wget",
        "fetch",
        "nc",
        "ncat",
        "netcat",
        "socat",
        "ssh",
        "scp",
        "rsync",
        "sftp",
        "ftp",
        "telnet",
        "Invoke-WebRequest",
        "iwr",
    }
)
