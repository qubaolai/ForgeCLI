"""依赖方向静态检查 (ADR-0053 按功能分包; 沿用 ADR-0004 §13, ADR-0016 §2 的互斥边界).

为什么要一个脚本而不是靠自觉: 分层违规都是"能跑通"的 —— import 一个上层模块永远不会
报错, 只会在半年后变成一团解不开的环. 把方向写成可执行断言, 违规在 `make ci` 就停下.

四类规则:

1. **包的层级** (LAYERS): 每个顶层包只能 import 比自己低的层. 层是从实际 import 图算出
   来的拓扑序, 写死在这里是为了让新加的一条反向 import 立刻被认出来, 而不是等它把两
   个包缠成一个环.
2. **第三方归属** (THIRD_PARTY_HOMES): 终端渲染, HTTP 框架与供应商 SDK 各有唯一的家;
   业务包里出现它们就是分层漏了.
3. **同层互斥** (SIBLING_BANS): 工具与安全互不相识 (ADR-0004 §2), 记忆与上下文不参与
   裁决 (ADR-0033, ADR-0041), 人机提示通道什么都不认识 (ADR-0043), 上游只认识 LlmGateway
   与 ToolRunObserver 抽象而不认识实现 (ADR-0028 规则 A1).
4. **无 import 环**: 模块级 import 图必须是有向无环的.

第 4 条为什么值得一个检查: import 环不一定会炸. 只要有一条 import 顺序恰好把每个
模块在被回头引用前初始化完, 环就一直静默存在 —— 直到有人动了某个 __init__ 或换了
第一个 import 它的入口. 已经吃过两次: 旧 application/llm/config 与 application/llm/gateway
的包门面各自藏了一个环, 都是靠顺序活着的. 顺序是运气, 不是设计.

环最常见的成因就是**急切转导出的包门面**: `a/__init__.py` import 了 `a.b`, 而 `a.c`
import `a.b` —— 于是 import `a.c` 会先跑 `a/__init__`, 再回头要还没初始化完的 `a.c`.
所以检查把"import a.b.c"记成同时指向 `a`, `a.b`, `a.b.c` 三条边: 包门面里的每一条
import 都是图上的真实边, 不是注释.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "forgecli"
ROOT = "forgecli"

# 顶层包的层级, 低的在前. 一个包只能 import 比自己层级低的包 (同包内部不限).
#
# 这张表是 2026-09-15 迁移时从 import 图算出的拓扑序; `core` 是跨包共用的词汇, 它下面
# 只有 `common`. 改动这张表要有理由: 把一个包往下挪等于承诺它不再认识上面的任何人.
LAYERS: tuple[frozenset[str], ...] = (
    frozenset({"common", "human_interaction"}),
    frozenset({"core", "memory"}),
    frozenset({"settings", "project", "prompt", "workspace", "planning"}),
    frozenset({"llm", "security", "recovery"}),
    frozenset({"conversation", "execution", "session"}),
    frozenset({"tools"}),
    frozenset({"agent"}),
    frozenset({"runtime"}),
    frozenset({"tui"}),
    frozenset({"web"}),
    frozenset({"cli"}),
)
LEVEL_OF: dict[str, int] = {
    package: level for level, packages in enumerate(LAYERS) for package in packages
}

# 第三方顶层模块 -> 允许 import 它的包前缀. 不在表里的第三方不受限.
# 空元组 = 任何地方都不许出现 (供应商 SDK 与备用 HTTP 客户端: 只走 llm.adapters 的 httpx).
THIRD_PARTY_HOMES: dict[str, tuple[str, ...]] = {
    "rich": ("tui", "web", "cli"),
    "prompt_toolkit": ("tui",),
    "typer": ("cli",),
    "fastapi": ("web",),
    "starlette": ("web",),
    "sse_starlette": ("web",),
    "uvicorn": ("web",),
    "httpx": ("llm.adapters",),
    "requests": (),
    "openai": (),
    "anthropic": (),
}

# 同层互斥: (包前缀, 不得 import 的包前缀, 豁免前缀, 说明).
SIBLING_BANS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    (
        "tools",
        "security",
        # 管线是唯一的装配点: 它串起机制, 安全, 审批与恢复 (ADR-0004 §5).
        ("tools.pipeline",),
        "工具是机制层, 不做安全决策 (ADR-0004 §2)",
    ),
    (
        "security",
        "tools",
        (),
        "安全是策略层, 不认识任何具体工具实现 (ADR-0004 §2)",
    ),
    # 上游只认识 LlmGateway 抽象, 不认识网关实现 (ADR-0011 §3.1, ADR-0028 规则 A1).
    # DefaultLlmGateway 拖着 provider 注册表, 凭证池, 重试环与治理件; 让 agent.loop
    # 直接 import 它, 一个循环用例就要装配整个 LLM 子系统.
    (
        "agent.loop",
        "llm.gateway.default_gateway",
        (),
        "AgentLoop 只依赖 LlmGateway 抽象, 不依赖网关实现 (ADR-0028 规则 A1)",
    ),
    (
        "conversation",
        "llm.gateway.default_gateway",
        (),
        "上下文管理只依赖 LlmGateway 抽象, 不依赖网关实现 (ADR-0028 规则 A1)",
    ),
    # 网关只认识 ModelProvider 抽象; 供应商 HTTP 适配由组合根注入. 这条边界是
    # ModelProvider 这个 ABC 存在的理由 (check_abstractions.py A1).
    (
        "llm.gateway",
        "llm.adapters",
        (),
        "网关只依赖 ModelProvider 抽象, 不依赖供应商适配 (ADR-0028 规则 A1)",
    ),
    # 记忆是模型可写且不经人确认的提示词输入. 它进不了裁决, 是整个 ADR-0033 敢做静默
    # 写入的承重理由 —— 所以这条边界必须是机器守得住的, 写在注释里不算.
    (
        "memory",
        "security",
        (),
        "记忆不参与任何安全裁决 (ADR-0033 决策 3)",
    ),
    # 上下文里装的东西有一半是模型自己写的 (记忆, 计划, 待办), 另一半是工具输出的转述.
    # 两者都不该有机会影响裁决 —— 与上面那条给记忆定的边界同一个理由, 只是范围更大.
    (
        "conversation",
        "security",
        (),
        "上下文组装不参与任何安全裁决 (ADR-0041)",
    ),
    (
        "agent.loop",
        "memory",
        (),
        "AgentLoop 不读写长期记忆 (ADR-0010 §影响)",
    ),
    # 工具链路只认识自己的输出抽象, 不认识另一头的实现 (ADR-0028 规则 A1).
    # 删掉它, ToolRunObserver 这个抽象就白留了 —— 协调器会顺着 import 直接认识事件总线.
    (
        "tools.pipeline",
        "agent",
        (),
        "工具链路不认识终端事件总线, 只发 ToolRunObserver (ADR-0016 §4.3)",
    ),
    # 人机提示通道是审批与 ask_user 共用的那一条队列 (ADR-0043 决策 3). 它承重的地方
    # 恰恰是**它什么都不认识**: 一旦 HumanPrompt 里出现 ApprovalScope 或 ToolPlan,
    # 下一次给审批加字段就会顺手让提问也拿到它, 而那个字段可能正是一档授权范围.
    #
    # 这条边界必须是机器守得住的 —— 写在注释里的"请保持哑"挡不住任何一次顺手.
    (
        "human_interaction",
        "security",
        (),
        "人机提示通道不认识授权词汇 (ADR-0043 决策 3 纪律 1)",
    ),
    (
        "human_interaction",
        "core.tool",
        (),
        "人机提示通道不认识工具计划 (ADR-0043 决策 3 纪律 1)",
    ),
)


def _imported_modules(tree: ast.AST) -> list[tuple[str, int]]:
    """收集本文件 import 的模块名与行号. `from . import x` 这类相对导入跳过."""
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.module, node.lineno))
    return found


def _package_path(relative: Path) -> str:
    """`tools/builtin/x.py` -> `tools.builtin.x`; `cli.py` -> `cli`."""
    parts = relative.with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _within(package: str, prefix: str) -> bool:
    return package == prefix or package.startswith(f"{prefix}.")


def _check_file(path: Path) -> list[str]:
    relative = path.relative_to(SRC)
    package = _package_path(relative)
    if not package:
        return []
    top = package.split(".")[0]
    if top not in LEVEL_OF:
        return [f"{relative} 所在的顶层包 {top} 不在 LAYERS 里"]
    problems: list[str] = []

    for module, lineno in _imported_modules(ast.parse(path.read_text("utf-8"), path)):
        location = f"{relative}:{lineno}"

        if module == ROOT or module.startswith(f"{ROOT}."):
            target = module[len(ROOT) + 1 :]
            target_top = target.split(".")[0]
            if not target_top:
                continue
            if target_top not in LEVEL_OF:
                problems.append(f"{location} import 了不在 LAYERS 里的包: {module}")
                continue
            if target_top != top and LEVEL_OF[target_top] >= LEVEL_OF[top]:
                problems.append(
                    f"{location} {top} (L{LEVEL_OF[top]}) 不能依赖 "
                    f"{target_top} (L{LEVEL_OF[target_top]}): import {module}"
                )
            for source_prefix, banned_prefix, exempt, why in SIBLING_BANS:
                if not _within(package, source_prefix):
                    continue
                if any(_within(package, prefix) for prefix in exempt):
                    continue
                if _within(target, banned_prefix):
                    problems.append(f"{location} {why}: import {module}")
            continue

        head = module.split(".")[0]
        homes = THIRD_PARTY_HOMES.get(head)
        if homes is None:
            continue
        if not any(_within(package, home) for home in homes):
            allowed = ", ".join(homes) or "任何地方"
            problems.append(
                f"{location} {head} 只许出现在 {allowed}, 这里是 {package}: import {module}"
            )

    return problems


def _module_name(relative: Path) -> str:
    """`llm/gateway/__init__.py` -> `forgecli.llm.gateway`."""
    parts = relative.with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join((ROOT, *parts))


def _source_modules() -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        found.append((_module_name(path.relative_to(SRC)), path))
    return found


def _import_graph() -> dict[str, set[str]]:
    """模块 -> 它 import 的模块. import a.b.c 同时记 a 与 a.b: 包 __init__ 会先跑."""
    known = {name for name, _ in _source_modules()}
    graph: dict[str, set[str]] = {}
    for me, path in _source_modules():
        targets: set[str] = set()
        for module, _ in _imported_modules(ast.parse(path.read_text("utf-8"), path)):
            if not module.startswith(f"{ROOT}."):
                continue
            prefix = ROOT
            for part in module.split(".")[1:]:
                prefix = f"{prefix}.{part}"
                if prefix in known and prefix != me:
                    targets.add(prefix)
        graph[me] = targets
    return graph


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """迭代式 Tarjan 求强连通分量; 元素数 > 1 的分量就是一个 import 环."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[list[str]] = []
    counter = 0

    for root in sorted(graph):
        if root in index:
            continue
        work: list[tuple[str, list[str]]] = [(root, sorted(graph[root]))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, pending = work[-1]
            descended = False
            while pending:
                target = pending.pop(0)
                if target not in index:
                    index[target] = low[target] = counter
                    counter += 1
                    stack.append(target)
                    on_stack.add(target)
                    work.append((target, sorted(graph.get(target, ()))))
                    descended = True
                    break
                if target in on_stack:
                    low[node] = min(low[node], index[target])
            if descended:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    found.append(sorted(component))
    return found


def _check_cycles() -> list[str]:
    problems: list[str] = []
    for component in sorted(_cycles(_import_graph())):
        members = " <-> ".join(name[len(ROOT) + 1 :] for name in component)
        problems.append(f"import 环: {members}")
    return problems


def main() -> int:
    problems: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        problems.extend(_check_file(path))
    problems.extend(_check_cycles())

    if problems:
        print("依赖方向检查未通过:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"依赖方向检查通过 ({len(LAYERS)} 层, {len(SIBLING_BANS)} 条互斥).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
