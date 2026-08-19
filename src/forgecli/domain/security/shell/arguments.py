"""一条命令的参数结构: 哪些参数是路径 (ADR-0013 §6.2).

`effects.py` 回答"这条命令碰文件系统的方式是读还是写", 这里回答"它碰的到底是哪几个参数".
两件事分开, 是因为它们的错法不同: 前者错了会漏掉一整类影响, 后者错了会**凭空造出目标**.

在这个模块出现之前, 判据只有一句 `not arg.startswith("-")`. 实测后果:

    grep -r skipUrl .          -> 读取 /ws/skipUrl      搜索词被当成文件
    sed -i.bak s/a/b/ f.txt    -> 写入 /ws/s/a/b        sed 表达式被当成要写的文件
    tar -czf out.tgz src       -> 写入 /ws/src          tar 只读 src, 这里说它要写
    find . -exec rm {} \\;      -> 读取 /ws/rm /ws/{}    exec 的内层命令被当成路径

这些假路径会一路走到审批框, 与真实目标混排成一份"读取 (6 项)". 用户学会忽略那份清单
之后, 真正危险的那一条也就跟着被忽略了 —— **一份掺假的清单比没有清单更糟**.

**这不是安全白名单.** 表里有没有一条命令, 只影响"它的参数怎么解读"; 允不允许碰某个路径
仍然由受保护路径检查, 模式预算与目标封闭性裁决. 表外命令按"参数结构未知"处理, 调用方
照旧把位置参数收进候选集 (`cat ~/.ssh/id_rsa` 这类必须被受保护路径检查看见), 只是那份
清单要按推测展示, 不能冒充事实.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forgecli.domain.security.shell.command_plan import CommandUnit

__all__ = ["ArgumentModel", "ClassifiedArguments", "classify_arguments"]


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


@dataclass(frozen=True)
class ClassifiedArguments:
    """一次分类的结果."""

    # 按参数结构判定出的路径候选.
    paths: tuple[str, ...] = ()
    # 表里有没有这条命令. False 时 paths 是"按老规矩收的位置参数", 只能当推测用.
    known: bool = False
    # 被识别为非路径而剔除掉的参数. 只用于诊断, 不进任何裁决.
    discarded: tuple[str, ...] = field(default=())


# 只覆盖 coding agent 真正会用的那些. 不追求完备 —— 表外命令有明确的降级路径, 而一张
# 假装完备的表会让人以为漏项不存在.
_MODELS: dict[str, ArgumentModel] = {}


def _register(names: tuple[str, ...], model: ArgumentModel) -> None:
    for name in names:
        _MODELS[name] = model


# grep 家族: 第一个位置参数是模式, 除非 -e / -f 已经给过模式.
_register(
    ("grep", "egrep", "fgrep", "rg", "ack", "ag", "ripgrep"),
    ArgumentModel(
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
)

# find: 起点目录写在第一个选项之前, 之后整段是表达式.
#
# `-exec` 的内层命令由 wrappers 提取成独立单元 (UnitOrigin.WRAPPER_INNER), 不在这里当
# 参数处理 —— 那条命令有自己的影响形态, 把 `rm` 当成一个路径名是这份清单最离谱的一处.
_register(
    ("find", "fd"),
    ArgumentModel(
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
)

# sed / awk: 第一个位置参数是脚本, 除非 -e / -f 已经给过.
_register(
    ("sed", "gsed"),
    ArgumentModel(
        value_options=frozenset({"e", "--expression"}),
        path_options=frozenset({"f", "--file"}),
        leading_non_paths=1,
        leading_supplied_by=frozenset({"e", "--expression", "f", "--file"}),
    ),
)
_register(
    ("awk", "gawk", "mawk", "nawk"),
    ArgumentModel(
        value_options=frozenset({"v", "--assign"}),
        path_options=frozenset({"f", "--file"}),
        leading_non_paths=1,
        leading_supplied_by=frozenset({"f", "--file"}),
    ),
)

# 归档: -f 的值是归档文件本身, 其余位置参数是被打包/解包的路径.
_register(
    ("tar", "gtar"),
    ArgumentModel(
        value_options=frozenset({"--format", "--owner", "--group"}),
        path_options=frozenset(
            {"f", "C", "--file", "--directory", "--exclude", "--exclude-from"}
        ),
    ),
)
_register(
    ("zip", "unzip", "7z", "7za"),
    ArgumentModel(path_options=frozenset({"d", "--output-dir"})),
)

_register(("sort",), ArgumentModel(path_options=frozenset({"o", "--output"})))

# xargs 的位置参数是"要跑的命令 + 它的参数", 由 wrappers 提取成独立单元处理.
# 留在这里当路径的话, `xargs rm -rf build` 会记一条名叫 rm 的读取目标.
_register(("xargs",), ArgumentModel(positionals_are_paths=False))
_register(("tee",), ArgumentModel(value_options=frozenset()))

# 参数与文件系统无关的命令. 它们在 _KNOWN_READERS 里, 于是位置参数会被当成读取路径:
# `echo hello` 记一条 /ws/hello, `sleep 5` 记一条 /ws/5.
_register(
    (
        "echo",
        "printf",
        "sleep",
        "seq",
        "yes",
        "date",
        "expr",
        "true",
        "false",
        "hostname",
        "uname",
        "id",
        "whoami",
        "groups",
        "uptime",
        "printenv",
        "locale",
        "which",
        "whereis",
    ),
    ArgumentModel(positionals_are_paths=False),
)


# 间接执行的占位符. `find -exec rm {} ;` 里的 `{}` 代表"find 找到的那个文件", 它自己
# 不是任何路径. 与具体命令无关, 所以在模型之外统一剔除.
_PLACEHOLDERS = frozenset({"{}", "{};"})


def classify_arguments(unit: CommandUnit) -> ClassifiedArguments:
    """把一个单元的 argv 分成路径候选与非路径.

    表外命令按老规矩把全部位置参数收进 `paths` 并标 `known=False`: 少收一条的后果是
    `cat ~/.ssh/id_rsa` 这类目标从受保护路径检查里消失, 那是往危险方向错. 多收一条的
    后果只是展示层要说清它是推测 —— 两种错法的代价不对称, 所以默认仍然多收.
    """
    model = _MODELS.get(unit.name)
    if model is None:
        bare = _bare_positionals(unit.argv)
        return ClassifiedArguments(
            paths=tuple(item for item in bare if item not in _PLACEHOLDERS),
            known=False,
            discarded=tuple(item for item in bare if item in _PLACEHOLDERS),
        )

    paths: list[str] = []
    discarded: list[str] = []
    positionals: list[str] = []
    leading_consumed = False
    seen_option = False
    pending: str | None = None

    for argument in unit.argv:
        if pending is not None:
            (paths if pending == "path" else discarded).append(argument)
            pending = None
            continue
        if _is_option(argument):
            seen_option = True
            head, _, inline = argument.partition("=")
            appetite, supplies_leading = _appetite(head, model)
            if supplies_leading:
                leading_consumed = True
            if inline:
                # `--file=x`: 值就在同一个 token 里, 不吃下一个参数.
                (paths if appetite == "path" else discarded).append(inline)
                continue
            pending = appetite
            continue
        if seen_option and not model.trailing_positionals_are_paths:
            discarded.append(argument)
            continue
        positionals.append(argument)

    if not model.positionals_are_paths:
        discarded.extend(positionals)
        positionals = []
    elif model.leading_non_paths and not leading_consumed:
        skipped = model.leading_non_paths
        discarded.extend(positionals[:skipped])
        positionals = positionals[skipped:]

    paths.extend(positionals)
    discarded.extend(item for item in paths if item in _PLACEHOLDERS)
    return ClassifiedArguments(
        paths=tuple(item for item in paths if item not in _PLACEHOLDERS),
        known=True,
        discarded=tuple(discarded),
    )


def _bare_positionals(argv: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(argument for argument in argv if not _is_option(argument))


def _is_option(argument: str) -> bool:
    """`-` 与 `--` 本身不是选项: 前者常指标准输入, 后者是选项终止符."""
    return argument.startswith("-") and argument not in ("-", "--")


def _appetite(head: str, model: ArgumentModel) -> tuple[str | None, bool]:
    """这个选项吃不吃下一个参数, 吃的是不是路径, 以及它顶不顶掉首位置参数.

    返回 ("path" | "skip" | None, 是否供给了首位置参数).
    """
    if head.startswith("--"):
        keys: tuple[str, ...] = (head,)
    else:
        # 聚合短选项 `-czf`: 逐字符看, 吃参数的只可能是最后一个匹配到的.
        keys = tuple(head[1:])
    appetite: str | None = None
    supplies_leading = False
    for key in keys:
        if key in model.leading_supplied_by:
            supplies_leading = True
        if key in model.path_options:
            appetite = "path"
        elif key in model.value_options:
            appetite = "skip"
    return appetite, supplies_leading
