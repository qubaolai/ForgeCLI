"""开放式穷举表必须标注分类与漏项方向 (ADR-0040 决策 7 与验收标准).

为什么值得一个脚本: 这类表的问题从来不是"写错了", 是**没人知道它错了会怎样**.

一张命令名表, 一张路径前缀表, 看上去都一样. 但 `_IRREVERSIBLE` 漏一项等于一次不可逆
操作不问人就跑了, `_LOW_VALUE_SUFFIXES` 漏一项只是噪音排得靠前一点. 这两件事的差别不在
代码里 —— 除非有人把它写下来.

所以规则是: 下面这些文件里, 每一个模块级的集合常量都要在紧邻它的注释里说清三件事:

1. 它是 ADR-0040 的哪一类 (A 派生 / B 非承重 / C 由运行期取代 / D 承重);
2. 表外的默认方向是什么;
3. 漏一项的后果是什么.

或者明确写它是**封闭**集合 —— 成员由协议或状态机定义, 加成员必须改调用者, 那种表不属于
本检查的范围 (ADR-0040 决策 9).

检查的是"有没有交代", 不是"交代得对不对" —— 后者只能靠评审. 但一个连自己漏了会怎样都
说不出来的表, 评审时一定会被问到同一个问题.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "forgecli"

# 安全裁决路径上那些放表的文件. 只看它们: 别处的常量漏了不会变成授权缺陷.
WATCHED: tuple[str, ...] = (
    "domain/security/hard_deny.py",
    "domain/security/shell/builtins.py",
    "domain/security/shell/command_plan.py",
    "domain/security/shell/commands.py",
    "domain/security/shell/wrappers.py",
    "domain/execution/environment.py",
    "application/security/analyzers/shell_effects.py",
    "infrastructure/security/protected_paths_builder.py",
)

# 认得出的交代. "封闭"那一条给 ADR-0040 决策 9 说的封闭集合用.
_EXPLAINED = re.compile(r"ADR-0040|封闭")

# 集合型字面量: 这类常量才是"表". 单个字符串, 数字, 正则不算.
_TABLE_NODES = (ast.Set, ast.Tuple, ast.List, ast.Dict)

# 这些名字不是表: 版本号, 上限, 单值常量.
SKIP: frozenset[str] = frozenset(
    {
        # 模块导出清单, 不是判据表.
        "__all__",
        "ENVIRONMENT_SANITIZATION_VERSION",
        "PARSER_VERSION",
        "MAX_WRAPPER_DEPTH",
    }
)


def _is_table(node: ast.AST) -> bool:
    """一个模块级赋值是不是"表"."""
    if isinstance(node, _TABLE_NODES):
        return True
    # frozenset({...}) / frozenset(...)
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        return name in ("frozenset", "set", "dict", "tuple", "list")
    return False


def _explained(lines: list[str], lineno: int) -> bool:
    """赋值上方紧邻的注释块里有没有交代. 中间隔了空行就不算 —— 那是别人的注释."""
    index = lineno - 2  # lineno 是 1 起的, 再往上一行
    block: list[str] = []
    while index >= 0 and lines[index].lstrip().startswith("#"):
        block.append(lines[index])
        index -= 1
    return bool(_EXPLAINED.search("\n".join(block)))


def main() -> int:
    problems: list[str] = []
    checked = 0
    for relative in WATCHED:
        path = SRC / relative
        if not path.exists():
            problems.append(f"{relative}: 文件不存在, 检查清单该更新了")
            continue
        text = path.read_text(encoding="utf-8")
        lines = text.split("\n")
        for node in ast.parse(text, filename=str(path)).body:
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
            else:
                continue
            if node.value is None or not _is_table(node.value):
                continue
            for name in targets:
                if name in SKIP:
                    continue
                checked += 1
                if not _explained(lines, node.lineno):
                    problems.append(
                        f"{relative}::{name} 没有交代它是哪一类, 表外默认什么, "
                        f"漏一项会怎样 (ADR-0040 决策 7)"
                    )

    if problems:
        print("开放式穷举表检查未通过:\n")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"开放式穷举表检查通过 ({checked} 张表都交代了分类与漏项方向).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
