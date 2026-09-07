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

**实现是怎么认出来的** (ADR-0048 决策 4): 显式继承之外, 还认结构化实现 —— Protocol
不要求继承, 只看形状, 所以 `class OsWorkspaceSnapshotProvider:` 不写基类也是
`WorkspaceSnapshotProvider` 的实现. 认定判据是**绑定**而不是形状: 这个类被传进了某个
声明为该 Protocol 的形参或属性. 绑定点在源码里指得出来, 而且它成不成立由 mypy 保证.
只比较方法名会把凑巧同名的类算成实现, 按类名开白名单则等于把判据关掉 —— 两者都不用.
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "forgecli"

# A3: 开放扩展点. 实现数量本身就是它们存在的理由, 新增实现不该改任何既有代码.
OPEN_EXTENSION_POINTS: frozenset[str] = frozenset(
    {
        "Tool",
        "CapabilityAnalyzer",
    }
)

# A1 的跨包形态: 抽象所在包 -> 实现所在包, 两者之间有 check_arch.py 守住的 SIBLING_BAN.
# 列在这里的抽象即使实现同层, 也承担依赖倒置 —— 删掉它上游就要跨过那条边界.
INVERTED_PACKAGE_BOUNDARIES: frozenset[str] = frozenset(
    {
        "ToolRunObserver",
        "LlmGateway",
    }
)

# 空实现前缀. 它们不构成 A2 的"第二个真实实现".
NULL_IMPL_PREFIXES: tuple[str, ...] = ("Null", "Noop", "Fake", "Pending")


@dataclass
class _Signature:
    """一个可调用体的形参注解. 位置形参按顺序, 用来解析位置实参."""

    order: list[str] = field(default_factory=list)
    annotations: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class _Index:
    classes: dict[str, tuple[str, str]] = field(default_factory=dict)
    ports: dict[str, tuple[str, str]] = field(default_factory=dict)
    inherited: dict[str, list[tuple[str, str, str]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    signatures: dict[str, _Signature] = field(default_factory=dict)
    attributes: dict[str, list[str]] = field(default_factory=dict)


def _base_names(node: ast.ClassDef) -> list[str]:
    names: list[str] = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
    return names


def _declared_types(node: ast.expr | None) -> list[str]:
    """注解里声明的类型名. 认 ``P``, ``P | None``, ``Optional[P]``.

    不下钻 ``Callable[..., P]`` / ``list[P]``: 那些位置上收的不是一个 P 实例, 把它们
    算成绑定点就会凭空多出实现, 而多出来的实现会让一个本该报错的抽象蒙混过去.
    """
    if node is None:
        return []
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _declared_types(node.left) + _declared_types(node.right)
    if isinstance(node, ast.Subscript):
        outer = _declared_types(node.value)
        return _declared_types(node.slice) if outer == ["Optional"] else []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return _declared_types(ast.parse(node.value, mode="eval").body)
        except SyntaxError:
            return []
    return []


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> _Signature:
    signature = _Signature()
    args = node.args
    for index, arg in enumerate([*args.posonlyargs, *args.args]):
        if index == 0 and arg.arg in ("self", "cls"):
            continue
        signature.order.append(arg.arg)
        signature.annotations[arg.arg] = _declared_types(arg.annotation)
    for arg in args.kwonlyargs:
        signature.annotations[arg.arg] = _declared_types(arg.annotation)
    return signature


def _collect(index: _Index, path: Path, relative: Path) -> ast.Module:
    """第一遍: 类, 端口, 显式继承, 以及构造函数与模块级函数的形参注解."""
    layer = relative.parts[0]
    tree = ast.parse(path.read_text("utf-8"), path)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            index.signatures.setdefault(node.name, _signature(node))
            continue
        if not isinstance(node, ast.ClassDef):
            continue
        index.classes[node.name] = (layer, str(relative))
        bases = _base_names(node)
        if "ABC" in bases or "Protocol" in bases:
            index.ports[node.name] = (layer, str(relative))
        for base in bases:
            if base not in ("ABC", "Protocol"):
                index.inherited[base].append((node.name, layer, str(relative)))
        for item in node.body:
            # 构造函数的形参就是这个类名下的签名: 调用点写的是类名, 不是 __init__.
            if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                index.signatures[node.name] = _signature(item)
            # dataclass 字段: `provider: WorkspaceSnapshotProvider` 也是一个绑定位置.
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                key = f"{node.name}.{item.target.id}"
                index.attributes[key] = _declared_types(item.annotation)
                index.signatures.setdefault(node.name, _Signature())
                index.signatures[node.name].annotations.setdefault(
                    item.target.id, _declared_types(item.annotation)
                )
                index.signatures[node.name].order.append(item.target.id)
    return tree


def _instantiated(node: ast.expr, locals_: dict[str, str]) -> str | None:
    """这个实参表达式给出的是哪个类的实例. 认不出来就返回 None, 不猜."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node, ast.Name):
        return locals_.get(node.id)
    return None


def _bindings(index: _Index, tree: ast.Module) -> list[tuple[str, str]]:
    """第二遍: 找出 (Protocol 名, 具体类名) 的绑定对."""
    found: list[tuple[str, str]] = []
    # 局部变量 -> 它持有的类. `p = OsX(...)` 之后 `f(port=p)` 仍然是一个绑定点.
    holders: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
        ):
            holders[node.targets[0].id] = node.value.func.id

    for node in ast.walk(tree):
        # `x: P = C(...)`
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            concrete = _instantiated(node.value, holders)
            if concrete is not None:
                for declared in _declared_types(node.annotation):
                    found.append((declared, concrete))
            continue
        if not isinstance(node, ast.Call):
            continue
        callee = node.func.id if isinstance(node.func, ast.Name) else None
        if callee is None:
            continue
        signature = index.signatures.get(callee)
        if signature is None:
            continue
        for position, argument in enumerate(node.args):
            if position >= len(signature.order):
                break
            concrete = _instantiated(argument, holders)
            if concrete is None:
                continue
            for declared in signature.annotations.get(signature.order[position], []):
                found.append((declared, concrete))
        for keyword in node.keywords:
            if keyword.arg is None:
                continue
            concrete = _instantiated(keyword.value, holders)
            if concrete is None:
                continue
            for declared in signature.annotations.get(keyword.arg, []):
                found.append((declared, concrete))
    return found


def _scan() -> tuple[dict[str, tuple[str, str]], dict[str, list[tuple[str, str, str]]]]:
    """返回 (抽象名 -> (层, 文件), 抽象名 -> [(实现名, 层, 文件)])."""
    index = _Index()
    trees: list[ast.Module] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        trees.append(_collect(index, path, path.relative_to(SRC)))

    impls: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for base, entries in index.inherited.items():
        impls[base].extend(entries)
    seen = {(port, impl) for port, entries in impls.items() for impl, _, _ in entries}
    for tree in trees:
        for port, concrete in _bindings(index, tree):
            if port not in index.ports or concrete == port:
                continue
            where = index.classes.get(concrete)
            # 只认本仓定义的类: 第三方类的层归属无从判断, 也不受本判据管辖.
            if where is None or (port, concrete) in seen:
                continue
            seen.add((port, concrete))
            impls[port].append((concrete, where[0], where[1]))
    return index.ports, impls


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
