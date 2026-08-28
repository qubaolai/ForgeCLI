"""目录树渲染: `fs_find` 不带 pattern 时的输出形态 (ADR-0029 A 类).

原先是独立工具 `fs_scan_tree`. 它与 `fs_list_files` 是**同一个动作的两个入口** ——
都在走目录, 只是一个渲染成树一个渲染成清单. 合并进 `fs_find` 之后这里只剩渲染,
不再是一个模型要选的工具.

留成独立模块而不是塞进 fs_find.py: 树渲染的预算控制与剪枝有自己的一套状态, 与 glob
展开混在一个文件里, 改其中一条时很难确定另一条会不会受影响.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.application.workspace.filesystem_view import PathFacts, PathKind

__all__ = [
    "DEFAULT_BUDGET",
    "DEFAULT_DEPTH",
    "MAX_BUDGET",
    "Scan",
    "empty_tree_message",
    "int_or",
    "walk",
]

DEFAULT_DEPTH = 3
DEFAULT_BUDGET = 600
MAX_BUDGET = 4000


@dataclass
class Scan:
    """一次扫描的累积状态.

    remaining 是**条目预算**而不是行数上限: 超预算就停在半路并如实说明, 而不是悄悄
    截断. 模型看到"已达上限"才会知道该缩小 path 再来一次, 否则它会把这份残缺的树当成
    完整结构去推断.
    """

    remaining: int
    lines: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    truncated: bool = False


def empty_tree_message(root: str) -> str:
    return (
        f"{root} 是空目录, 或者其中只有被默认忽略的生成目录 "
        "(.git, node_modules, target 一类); 需要它们请传 include_ignored=true."
    )


def int_or(raw: object, fallback: int) -> int:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else fallback


def walk(
    context: ExecutionContext,
    directory: str,
    *,
    prefix: str,
    depth_left: int,
    include_ignored: bool,
    scan: Scan,
) -> None:
    """深度优先渲染一层, 目录在前, 同类按名字排序.

    排序是刻意的: 同一个目录扫两次要给出同一棵树, 否则 plan_hash 会随目录项的返回顺序
    变化, 同一次批准也就绑不住第二次调用.
    """
    for name, facts in _children(context, directory, include_ignored=include_ignored):
        if scan.remaining <= 0:
            scan.truncated = True
            return
        scan.remaining -= 1
        scan.paths.append(facts.realpath or f"{directory}/{name}")
        if facts.kind is not PathKind.DIRECTORY:
            scan.lines.append(f"{prefix}{name}{_size_of(facts)}")
            continue
        if depth_left <= 1:
            # 深度到头. 报直接子项数而不是静默停住 —— "空目录"和"没展开"长得完全不同,
            # 混起来模型会以为这条路走到头了.
            held = len(
                _children(context, facts.realpath, include_ignored=include_ignored)
            )
            suffix = f"  ({held} 项未展开)" if held else "  (空)"
            scan.lines.append(f"{prefix}{name}/{suffix}")
            continue
        scan.lines.append(f"{prefix}{name}/")
        walk(
            context,
            facts.realpath,
            prefix=f"{prefix}  ",
            depth_left=depth_left - 1,
            include_ignored=include_ignored,
            scan=scan,
        )


def _children(
    context: ExecutionContext, directory: str, *, include_ignored: bool
) -> tuple[tuple[str, PathFacts], ...]:
    found: list[tuple[str, PathFacts]] = []
    root = context.primary_root
    for name in context.filesystem.list_dir(directory):
        child = f"{directory.rstrip('/')}/{name}"
        # 忽略判断问视图, 不在这里列名单: 树视图与 glob 展开必须给出同一个仓库形状,
        # 两份判断一定会走偏, 而走偏的后果是模型据此得出的结论互相矛盾.
        if not include_ignored and context.filesystem.is_ignored(child, root=root):
            continue
        facts = context.filesystem.facts(child)
        if not facts.exists:
            continue
        found.append((name, facts))
    found.sort(key=lambda item: (item[1].kind is not PathKind.DIRECTORY, item[0]))
    return tuple(found)


def _size_of(facts: PathFacts) -> str:
    """文件大小影响"要不要读它", 所以进树; 目录不报, 那只是目录项本身的字节数."""
    size = facts.size
    if size <= 0:
        return ""
    if size < 1024:
        return f"  {size}B"
    if size < 1024 * 1024:
        return f"  {size / 1024:.1f}K"
    return f"  {size / (1024 * 1024):.1f}M"
