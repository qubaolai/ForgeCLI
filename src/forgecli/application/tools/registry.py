"""ToolRegistry: 工具注册与目录视图 (ADR-0004 §2 / §7).

注册表**不认识 mode**. 它只做三件事: 按名字取工具, 按名字取 spec, 按外部谓词过滤出
一份带快照哈希的目录. "plan 档能看见哪些工具"是 application 层的谓词, 不是注册表的
知识 —— 否则每加一档模式都要改注册表, 每加一个工具都要想起来改能力门.

不存在未注册的执行通道: 没有注册, 没有 ToolPlan, 没有授权信封, 就没有执行.
"""

from __future__ import annotations

from forgecli.application.tools.tool import Tool
from forgecli.domain.tool.catalog import CatalogQuery, ToolCatalog
from forgecli.domain.tool.spec import ToolSpec
from forgecli.shared.errors import ForgeError

__all__ = ["DuplicateToolError", "ToolRegistry", "UnknownToolError"]


class UnknownToolError(ForgeError):
    """请求了未注册的工具. 协调器把它翻成 tool_unavailable observation."""

    def __init__(self, name: str) -> None:
        super().__init__(f"未注册的工具: {name}")
        self.tool_name = name


class DuplicateToolError(ForgeError):
    """同名工具重复注册. 装配期错误, 直接抛给开发者."""

    def __init__(self, name: str) -> None:
        super().__init__(f"工具名重复注册: {name}")
        self.tool_name = name


class ToolRegistry:
    """进程内的工具注册表. 装配期填充, 运行期只读."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        name = tool.spec.name
        if name in self._tools:
            raise DuplicateToolError(name)
        self._tools[name] = tool

    def register_all(self, tools: tuple[Tool, ...]) -> None:
        for tool in tools:
            self.register(tool)

    def contains(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownToolError(name) from None

    def describe(self, name: str) -> ToolSpec:
        return self.get(name).spec

    def describe_all(self) -> tuple[ToolSpec, ...]:
        """全部已注册的 spec, 按名字排序. **不是目录** —— 没有快照哈希.

        给展示用: 界面要把历史事件里的工具名翻成中文, 而那些调用可能发生在换模式之前,
        按当前模式过滤出来的目录里根本没有它们.

        刻意不返回 `ToolCatalog`: 那个类型的每一次出现都进模型请求记录, 事件与授权信封
        (见它的模块说明). 让一次纯展示查询也产出一份快照哈希, 等于在审计里多出一份没有
        任何调用与之对应的目录.
        """
        return tuple(sorted((tool.spec for tool in self._tools.values()), key=_by_name))

    def list(self, query: CatalogQuery) -> ToolCatalog:
        """按谓词过滤出目录快照. 谓词只看 ToolSpec, 看不到工具实现."""
        entries = tuple(
            tool.spec for tool in self._tools.values() if query.predicate(tool.spec)
        )
        return ToolCatalog(entries=entries, reason_tag=query.reason_tag)


def _by_name(spec: ToolSpec) -> str:
    return spec.name
