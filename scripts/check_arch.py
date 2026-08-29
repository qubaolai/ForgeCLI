"""依赖方向静态检查 (ADR-0002 轻量 DDD, ADR-0004 §13, ADR-0016 §2).

为什么要一个脚本而不是靠自觉: 分层违规都是"能跑通"的 —— import 一个下层模块永远不会
报错, 只会在半年后变成一团解不开的环. 把方向写成可执行断言, 违规在 `make ci` 就停下.

三类规则:

1. **层间方向**: domain 不认识任何人; application 只认识 domain; infrastructure 与
   interfaces 可以向下, 但 infrastructure 不认识 interfaces.
2. **框架隔离**: domain 与 application 不得 import Rich, prompt_toolkit 或任何供应商
   SDK / HTTP 客户端. 终端与协议细节留在 interfaces / infrastructure.
3. **工具与安全互不相识** (ADR-0004 §2): application/tools 不 import
   application/security, 反之亦然; 唯一装配点是 application/tool_request.
4. **无 import 环**: 模块级 import 图必须是有向无环的.

第 4 条为什么值得一个检查: import 环不一定会炸. 只要有一条 import 顺序恰好把每个
模块在被回头引用前初始化完, 环就一直静默存在 —— 直到有人动了某个 __init__ 或换了
第一个 import 它的入口. 已经吃过两次: application/llm/config 与 application/llm/gateway
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

# 每一层不允许 import 的层.
LAYER_BANS: dict[str, tuple[str, ...]] = {
    "domain": ("application", "infrastructure", "interfaces"),
    "application": ("infrastructure", "interfaces"),
    "infrastructure": ("interfaces",),
    "interfaces": (),
}

# domain 与 application 不得碰的第三方: 终端渲染, 交互输入, HTTP 与供应商 SDK.
BANNED_THIRD_PARTY: tuple[str, ...] = (
    "rich",
    "prompt_toolkit",
    "httpx",
    "requests",
    "openai",
    "anthropic",
)
THIRD_PARTY_FREE_LAYERS = ("domain", "application")

# 模块级互斥: (包前缀, 不得 import 的包前缀, 说明).
SIBLING_BANS: tuple[tuple[str, str, str], ...] = (
    (
        "application.tools",
        "application.security",
        "工具是机制层, 不做安全决策 (ADR-0004 §2)",
    ),
    (
        "application.security",
        "application.tools",
        "安全是策略层, 不认识任何具体工具实现 (ADR-0004 §2)",
    ),
    # 人工 Shell 是独立信任通道. 两条路径互相能看见, 就一定会有人把它们接起来:
    # "人工 Shell 顺便记一条 learned allow rule" 看着贴心, 实际是让用户手敲的命令
    # 替 Agent 拿到授权 (ADR-0017 §2).
    # 上游只认识 LlmGateway 端口, 不认识网关实现 (ADR-0011 §3.1, ADR-0028 规则 A1).
    # DefaultLlmGateway 拖着 provider 注册表, 凭证池, 重试环与治理件; 让 agent_loop
    # 直接 import 它, 一个循环用例就要装配整个 LLM 子系统.
    #
    # 2026-08-24: 原先还有一条 application.security -> llm.gateway.default_gateway,
    # 理由是"一个分类器用例就要装配整个 LLM 子系统". ADR-0030 删掉了 LLM 安全分类器,
    # security 现在零 llm 引用, 那条禁令的理由随之消失, 已删除 —— 留一条理由已经不成立
    # 的规则, 下一个读它的人会照着那条不存在的依赖去理解架构.
    (
        "application.agent_loop",
        "application.llm.gateway.default_gateway",
        "AgentLoop 只依赖 LlmGateway 端口, 不依赖网关实现 (ADR-0028 规则 A1)",
    ),
    (
        "application.context",
        "application.llm.gateway.default_gateway",
        "上下文管理只依赖 LlmGateway 端口, 不依赖网关实现 (ADR-0028 规则 A1)",
    ),
    # 记忆是模型可写且不经人确认的提示词输入. 它进不了裁决, 是整个 ADR-0033 敢做静默
    # 写入的承重理由 —— 所以这条边界必须是机器守得住的, 写在注释里不算.
    (
        "application.memory",
        "application.security",
        "记忆不参与任何安全裁决 (ADR-0033 决策 3)",
    ),
    (
        "application.agent_loop",
        "application.memory",
        "AgentLoop 不读写长期记忆 (ADR-0010 §影响)",
    ),
    # 工具链路只认识自己的输出端口, 不认识端口另一头的实现 (ADR-0028 规则 A1).
    # 删掉这两条, ToolRunObserver 与 ToolAuditSink 这两个抽象就白留了 —— 协调器会
    # 顺着同层的 import 直接认识事件总线与会话写入口.
    (
        "application.tool_request",
        "application.agent_run",
        "工具链路不认识终端事件总线, 只发 ToolRunObserver (ADR-0016 §4.3)",
    ),
    (
        "application.tool_request",
        "application.session",
        "工具链路不认识会话写入口, 只发 ToolAuditSink (ADR-0028 规则 A1)",
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


def _layer_of(relative: Path) -> str:
    return relative.parts[0]


def _package_path(relative: Path) -> str:
    """`application/tools/builtin/x.py` -> `application.tools.builtin.x`."""
    return ".".join(relative.with_suffix("").parts)


def _check_file(path: Path) -> list[str]:
    relative = path.relative_to(SRC)
    layer = _layer_of(relative)
    if layer not in LAYER_BANS:
        return []
    package = _package_path(relative)
    problems: list[str] = []

    for module, lineno in _imported_modules(ast.parse(path.read_text("utf-8"), path)):
        location = f"{relative}:{lineno}"

        if module.startswith(f"{ROOT}."):
            target_layer = module.split(".")[1]
            if target_layer in LAYER_BANS[layer]:
                problems.append(
                    f"{location} {layer} 不能依赖 {target_layer}: import {module}"
                )
            target_package = module[len(ROOT) + 1 :]
            for source_prefix, banned_prefix, why in SIBLING_BANS:
                if package.startswith(source_prefix) and target_package.startswith(
                    banned_prefix
                ):
                    problems.append(f"{location} {why}: import {module}")
            continue

        if layer in THIRD_PARTY_FREE_LAYERS:
            top = module.split(".")[0]
            if top in BANNED_THIRD_PARTY:
                problems.append(
                    f"{location} {layer} 不能依赖第三方 {top}: import {module}"
                )

    return problems


def _module_name(relative: Path) -> str:
    """`application/llm/gateway/__init__.py` -> `forgecli.application.llm.gateway`."""
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
    print("依赖方向检查通过.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
