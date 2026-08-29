"""抽象保留判据检查 (ADR-0028 规则 A).

为什么要一个脚本: "只有一个实现的抽象"从来不会报错, 它只会让每一次改动都要同时改
端口, 实现和 wiring 三处. 把判据写成可执行断言, 新加的空抽象在 `make ci` 就停下.

一个 ABC / Protocol 只在满足以下任一条件时保留:

- **A1 依赖倒置**: 删掉它会让上游包依赖一个更靠外的包. 跨层 (application ->
  infrastructure / interfaces) 与跨包 (check_arch.py SIBLING_BANS 守住的
  application 内部边界) 都算. 边界必须是机器守得住的 —— 只写在注释里的
  "依赖方向"不构成 A1.
- **A2 多实现**: 有两个及以上真实运行时实现. `Null*` / `Noop*` 空实现不算 ——
  它表达的是"可选依赖", 用 `X | None` 或一个默认实例表达更直接.
- **A3 开放扩展点**: 见 OPEN_EXTENSION_POINTS.

不满足则应删除 ABC, 直接依赖具体类.
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "forgecli"

# A3: 开放扩展点. 实现数量本身就是它们存在的理由, 新增实现不该改任何既有代码.
OPEN_EXTENSION_POINTS: frozenset[str] = frozenset(
    {
        "Tool",
        "CapabilityAnalyzer",
        "ModelSelection",
    }
)

# A1 的跨包形态: 抽象所在包 -> 实现所在包, 两者之间有 check_arch.py 守住的 SIBLING_BAN.
# 列在这里的抽象即使实现同层, 也承担依赖倒置 —— 删掉它上游就要跨过那条边界.
INVERTED_PACKAGE_BOUNDARIES: frozenset[str] = frozenset(
    {
        "ToolRunObserver",
        "ToolAuditSink",
        "LlmGateway",
    }
)

# 空实现前缀. 它们不构成 A2 的"第二个真实实现".
NULL_IMPL_PREFIXES: tuple[str, ...] = ("Null", "Noop", "Fake", "Pending")


def _base_names(node: ast.ClassDef) -> list[str]:
    names: list[str] = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
    return names


def _scan() -> tuple[dict[str, tuple[str, str]], dict[str, list[tuple[str, str, str]]]]:
    """返回 (抽象名 -> (层, 文件), 抽象名 -> [(实现名, 层, 文件)])."""
    ports: dict[str, tuple[str, str]] = {}
    impls: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(SRC)
        layer = relative.parts[0]
        for node in ast.walk(ast.parse(path.read_text("utf-8"), path)):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = _base_names(node)
            if "ABC" in bases or "Protocol" in bases:
                ports[node.name] = (layer, str(relative))
            for base in bases:
                if base not in ("ABC", "Protocol"):
                    impls[base].append((node.name, layer, str(relative)))
    return ports, impls


def _violations(
    ports: dict[str, tuple[str, str]],
    impls: dict[str, list[tuple[str, str, str]]],
) -> list[str]:
    problems: list[str] = []
    for name, (port_layer, port_file) in sorted(ports.items()):
        if name in OPEN_EXTENSION_POINTS:  # A3
            continue
        if name in INVERTED_PACKAGE_BOUNDARIES:  # A1 跨包
            continue
        found = impls.get(name, [])
        if any(layer != port_layer for _, layer, _ in found):  # A1
            continue
        real = [impl for impl, _, _ in found if not impl.startswith(NULL_IMPL_PREFIXES)]
        if len(real) >= 2:  # A2
            continue
        listed = ", ".join(impl for impl, _, _ in found) or "无实现"
        problems.append(
            f"{port_file}:{name} 同层且只有一个真实实现 ({listed}); "
            "按 ADR-0028 规则 A 应删除抽象, 直接依赖具体类"
        )
    return problems


def main() -> int:
    ports, impls = _scan()
    problems = _violations(ports, impls)
    if problems:
        print("抽象保留判据检查未通过 (ADR-0028 规则 A):", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"抽象保留判据检查通过 ({len(ports)} 个抽象).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
