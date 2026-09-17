"""工具池 (ADR-0054 决策 2; 对应 tools.ts 的 getAllBaseTools / getTools /
assembleToolPool).

内置总表由组合根给 (它拿得到各工具要的依赖); 这里只做过滤, 合并, 去重与查找.
"""

from __future__ import annotations

from forgecli.common.observability.log import get_log
from forgecli.core.permissions import ToolPermissionContext
from forgecli.core.tool.names import normalize_legacy_tool_name
from forgecli.core.tool.tool import Tool
from forgecli.security.permissions import get_deny_rule_for_tool

__all__ = [
    "assemble_tool_pool",
    "filter_tools_by_deny_rules",
    "find_tool_by_name",
    "get_tools",
    "tool_matches_name",
]

_log = get_log(__name__)


def tool_matches_name(tool: Tool, name: str) -> bool:
    if tool.name == name or name in tool.aliases:
        return True
    return normalize_legacy_tool_name(name) == tool.name


def find_tool_by_name(tools: tuple[Tool, ...], name: str) -> Tool | None:
    for tool in tools:
        if tool_matches_name(tool, name):
            return tool
    return None


def filter_tools_by_deny_rules(
    tools: tuple[Tool, ...], permission: ToolPermissionContext
) -> tuple[Tool, ...]:
    """整工具 deny 规则命中的不进池: 模型看不见就不会请求."""
    kept: list[Tool] = []
    for tool in tools:
        if get_deny_rule_for_tool(permission, tool) is None:
            kept.append(tool)
    return tuple(kept)


def get_tools(
    base_tools: tuple[Tool, ...], permission: ToolPermissionContext
) -> tuple[Tool, ...]:
    allowed = filter_tools_by_deny_rules(base_tools, permission)
    return tuple(tool for tool in allowed if tool.is_enabled())


def assemble_tool_pool(
    base_tools: tuple[Tool, ...],
    permission: ToolPermissionContext,
    external_tools: tuple[Tool, ...] = (),
) -> tuple[Tool, ...]:
    """内置 + 外部 (将来的 MCP), 各按名字排序, 按名字去重, 内置优先."""
    builtin = sorted(get_tools(base_tools, permission), key=_by_name)
    external = sorted(
        filter_tools_by_deny_rules(external_tools, permission), key=_by_name
    )
    seen: set[str] = set()
    pool: list[Tool] = []
    for tool in builtin + external:
        if tool.name in seen:
            _log.warning("tools.duplicate_name_dropped", tool=tool.name)
            continue
        seen.add(tool.name)
        pool.append(tool)
    return tuple(pool)


def _by_name(tool: Tool) -> str:
    return tool.name
