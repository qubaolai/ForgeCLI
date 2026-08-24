"""模型可读正文的归属检查 (ADR-0031).

为什么要一个脚本: 往 transcript 里加一句话是**最容易**的改动 —— 一个 f-string 就完事,
没有任何一层会说话. 提示词照常渲染, 测试照常绿, 指纹照常稳定, 只是这句话从此不受版本
管辖, 也不在任何人复核 `domain/prompt/text.py` 时出现在视野里.

规则: 送进模型上下文的参数里, 不得出现含中文的字符串字面量. 正文一律从
`domain/prompt/text.py` 取 —— 那里有版本, 有指纹快照, 而且全部正文在一屏之内, 彼此矛盾
才看得出来.

"送进模型上下文"由 SINKS 列举: 都是构造模型消息或提示词块的类型, 不是"看起来像提示词
的函数". 判据必须是机器认得出的, 不能靠命名习惯.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "forgecli"
CJK = re.compile(r"[一-鿿]")

# 构造类型 -> 要检查的参数. () 表示全部参数.
SINKS: dict[str, tuple[str, ...]] = {
    "TextBlock": (),
    "ToolResultBlock": ("content",),
    "LoopObservation": ("content",),
    "PromptBlock": ("heading", "body"),
}

# 关键字实参: 出现在任何调用上都要查.
SINK_KEYWORDS: frozenset[str] = frozenset({"system_prompt"})

# 正文的唯一出处. 它自己当然全是中文字面量.
CATALOG = SRC / "domain" / "prompt" / "text.py"


def _cjk_literals(node: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and CJK.search(child.value)
        ):
            found.append((child.lineno, child.value.replace("\n", " ")[:48]))
    return found


def _callee_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _check_file(path: Path) -> list[str]:
    problems: list[str] = []
    relative = path.relative_to(SRC)
    for node in ast.walk(ast.parse(path.read_text("utf-8"), path)):
        if not isinstance(node, ast.Call):
            continue
        name = _callee_name(node.func)
        wanted = SINKS.get(name)
        checked: list[tuple[str, ast.expr]] = []
        if wanted is not None:
            checked.extend((f"{name} 的位置参数", arg) for arg in node.args)
            checked.extend(
                (f"{name}.{kw.arg}", kw.value)
                for kw in node.keywords
                if kw.arg is not None and (not wanted or kw.arg in wanted)
            )
        checked.extend(
            (f"{name or '调用'} 的 {kw.arg}", kw.value)
            for kw in node.keywords
            if kw.arg in SINK_KEYWORDS
        )
        for label, expr in checked:
            for lineno, snippet in _cjk_literals(expr):
                problems.append(
                    f"{relative}:{lineno} {label} 直接写了中文字面量 {snippet!r}; "
                    "正文放 domain/prompt/text.py (ADR-0031)"
                )
    return problems


def main() -> int:
    problems: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts or path == CATALOG:
            continue
        problems.extend(_check_file(path))

    if problems:
        print("模型可读正文归属检查未通过 (ADR-0031):", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print("模型可读正文归属检查通过.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
