"""依赖声明与真实 import 的一致性检查 (ADR-0040 决策 11).

为什么值得一个脚本: 依赖清单的两种错法都不会让任何测试变红.

**声明少了**会跑得好好的, 因为传递依赖把包装进了环境. `interfaces/web/app.py` 直接
`from starlette...` 和 `from pydantic...`, 而 pyproject 里两个都没有 —— 它们是
fastapi 拖进来的. 今天能 import 是 fastapi 当前版本的实现细节: 哪天它换了 web 底座或者
放宽了 pydantic 约束, 断的是我们的 import, 而我们的 pyproject 一个字都没改. 装环境的人
也读不出来这个包是我们要的还是顺带的.

**声明多了**同样安静. `pathspec` 在依赖表里躺着但全库零 import; `tree-sitter` 被钉成
`>=0.26,<0.27`, 而我们只 import language-pack, 那个上界是在替 pack 决定它能配哪个版本的
运行时. 多余的声明照样要走安装, 锁定, 许可证审查和 CVE 跟踪, 只是没人再记得它是为什么
进来的.

两条规则:

1. `src/forgecli` 里每一个第三方顶层 import, 都要在 `[project].dependencies` 里有对应的
   直接声明;
2. 每一条直接声明, 都要在 `src/forgecli` 里有 import 消费者 —— 除非它在下面的
   `NO_IMPORT_CONSUMER` 里带着理由 (例如只作为命令行 entry point 被调起的包).

只看 `src/`. 测试与工具链的依赖是 dev 组的事; 装到用户机器上的是
`[project].dependencies`, 这个脚本守的就是后面这一份.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from collections import defaultdict
from importlib.metadata import packages_distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "forgecli"
PYPROJECT = ROOT / "pyproject.toml"

# 声明了但 src 里没有 import 的包, 必须写在这里并说明理由. 留空是正常状态:
# 一条都不需要豁免, 说明依赖表和源码是对上的.
#
# 什么样的理由算数: "正常运行时会调起它的 entry point" (例如构建后端, 命令行工具),
# "在可选路径里延迟 import 所以静态扫不到". 什么不算: "以后会用", "怕删错".
NO_IMPORT_CONSUMER: dict[str, str] = {}

# PEP 508 的依赖项写法很宽 ("name (>=1,<2)", "name>=1", 'name; extra == "x"').
# 我们只需要开头那个包名.
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _canonical(name: str) -> str:
    """PEP 503 的名字归一: Jinja2, RapidFuzz, prompt_toolkit 三种写法都要能对上."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared() -> dict[str, str]:
    """归一后的包名 -> pyproject 里的原始写法 (报错时按用户看得到的样子打印)."""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    declared: dict[str, str] = {}
    for requirement in data["project"]["dependencies"]:
        matched = _REQUIREMENT_NAME.match(requirement)
        if matched is None:
            raise SystemExit(f"读不懂的依赖项: {requirement!r}")
        declared[_canonical(matched.group(1))] = matched.group(1)
    return declared


def _third_party_imports() -> dict[str, set[Path]]:
    """src 里的第三方顶层模块名 -> 哪些文件 import 了它.

    相对 import (`from .x import y`) 不看: 它按定义是包内的.
    """
    found: dict[str, set[Path]] = defaultdict(set)
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module] if node.level == 0 and node.module else []
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top == "forgecli" or top in sys.stdlib_module_names:
                    continue
                found[top].add(path.relative_to(ROOT))
    return found


def main() -> int:
    declared = _declared()
    imports = _third_party_imports()
    # 顶层模块名到发行包名的映射只有安装后的元数据知道 (charset_normalizer 来自
    # charset-normalizer, tree_sitter_language_pack 来自 tree-sitter-language-pack).
    # 猜是猜不出来的, 所以这个脚本要在装好依赖的环境里跑.
    module_to_dists = packages_distributions()

    problems: list[str] = []
    consumed: set[str] = set()

    for module, files in sorted(imports.items()):
        dists = module_to_dists.get(module)
        if not dists:
            where = ", ".join(str(f) for f in sorted(files))
            problems.append(
                f"认不出 {module!r} 属于哪个发行包 (import 于 {where}); "
                f"依赖没装, 或者它是个新加的包 —— 先 poetry install 再跑."
            )
            continue
        canonical = {_canonical(dist) for dist in dists}
        matched = canonical & declared.keys()
        if matched:
            consumed |= matched
            continue
        where = ", ".join(str(f) for f in sorted(files))
        suggestion = sorted(canonical)[0]
        problems.append(
            f"{module!r} 被直接 import 却不是直接依赖 (见 {where}); "
            f"它现在只是传递依赖. 请把 {suggestion!r} 写进 [project].dependencies."
        )

    for canonical, original in sorted(declared.items()):
        if canonical in consumed or canonical in NO_IMPORT_CONSUMER:
            continue
        problems.append(
            f"{original!r} 是直接依赖却没有任何 src 消费者. 删掉它, "
            f"或者写进 scripts/check_deps.py 的 NO_IMPORT_CONSUMER 并说明理由."
        )

    for canonical in sorted(NO_IMPORT_CONSUMER):
        if canonical not in declared:
            problems.append(
                f"NO_IMPORT_CONSUMER 里的 {canonical!r} 已经不是直接依赖了, "
                f"把这条豁免一起删掉."
            )

    if problems:
        print("依赖声明检查未通过:\n")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"依赖声明检查通过 ({len(declared)} 个直接依赖, 全部有 src 消费者).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
