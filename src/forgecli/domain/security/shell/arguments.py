"""一条命令的参数结构: 哪些参数是路径 (ADR-0013 §6.2, ADR-0028 规则 D).

参数模型本身登记在 `commands.py` 的 `CommandFacts.arguments` —— 一条命令一条记录.
这里只做**应用**: 拿模型把一个单元的 argv 分成路径候选与非路径.

`effects.py` 回答"这条命令碰文件系统的方式是读还是写", 这里回答"它碰的到底是哪几个
参数". 两件事分开, 是因为它们的错法不同: 前者错了会漏掉一整类影响, 后者错了会**凭空
造出目标** —— 见 `commands.py` 的模块说明.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forgecli.domain.security.shell.command_plan import CommandUnit
from forgecli.domain.security.shell.commands import ArgumentModel, facts_for

__all__ = ["ArgumentModel", "ClassifiedArguments", "classify_arguments"]


@dataclass(frozen=True)
class ClassifiedArguments:
    """一次分类的结果."""

    # 按参数结构判定出的路径候选.
    paths: tuple[str, ...] = ()
    # 表里有没有这条命令. False 时 paths 是"按老规矩收的位置参数", 只能当推测用.
    known: bool = False
    # 被识别为非路径而剔除掉的参数. 只用于诊断, 不进任何裁决.
    discarded: tuple[str, ...] = field(default=())


# 间接执行的占位符. `find -exec rm {} ;` 里的 `{}` 代表"find 找到的那个文件", 它自己
# 不是任何路径. 与具体命令无关, 所以在模型之外统一剔除.
_PLACEHOLDERS = frozenset({"{}", "{};"})


def classify_arguments(unit: CommandUnit) -> ClassifiedArguments:
    """把一个单元的 argv 分成路径候选与非路径.

    表外命令按老规矩把全部位置参数收进 `paths` 并标 `known=False`: 少收一条的后果是
    `cat ~/.ssh/id_rsa` 这类目标从受保护路径检查里消失, 那是往危险方向错. 多收一条的
    后果只是展示层要说清它是推测 —— 两种错法的代价不对称, 所以默认仍然多收.
    """
    model = facts_for(unit.name).arguments
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
