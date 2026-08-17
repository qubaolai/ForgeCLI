"""工具机制层 (ADR-0004 §1): 注册, 计划, 执行, 取消与结果归一化.

这一层**不拥有**任何策略: 没有 mode 判断, 没有规则匹配, 没有审批调用, 没有分类器调用.
判定原则是"凡是要不要做的判断都不属于工具系统; 凡是怎么做的机制都属于工具系统".
"""

from forgecli.application.tools.registry import (
    DuplicateToolError,
    ToolRegistry,
    UnknownToolError,
)
from forgecli.application.tools.runtime import ToolRuntime
from forgecli.application.tools.tool import Tool, ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext

__all__ = [
    "DuplicateToolError",
    "ExecutionContext",
    "Tool",
    "ToolInvocationRequest",
    "ToolRegistry",
    "ToolRuntime",
    "UnknownToolError",
]
