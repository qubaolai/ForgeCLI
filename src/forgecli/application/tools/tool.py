"""Tool: 工具实现的统一契约 (ADR-0004 §2).

两段接口, 职责泾渭分明:

- ``prepare``: 只读. 把原始入参变成结构化, 可裁决的 ToolPlan, 或返回 PreparationError.
  不启动进程, 不访问网络, 不修改任何东西.
- ``perform``: 真正干活. **不叫 execute 是有意的** —— ToolRuntime.execute 是唯一带授权
  校验的入口, perform 只在校验通过后被调用, 它自己不认识授权信封, 也不该认识.

工具实现中不得出现 mode 判断, 规则匹配, 审批调用和分类器调用 (ADR-0004 §1 / §13).
这条由 scripts/check_arch.py 静态检查, 不靠人工评审.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.errors import PreparationError
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.tool.result import ToolResult
from forgecli.domain.tool.spec import ToolSpec
from forgecli.shared.cancellation import CancelToken

__all__ = ["Tool", "ToolInvocationRequest"]


@dataclass(frozen=True)
class ToolInvocationRequest:
    """一次调用请求: 模型给的原始入参 + 定位信息. arguments 未经校验, 不可信任."""

    invocation_id: str
    tool_name: str
    arguments: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )
    turn_id: str = ""
    session_id: str = ""
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        if not self.invocation_id.strip():
            raise ValueError("ToolInvocationRequest.invocation_id 不能为空")
        if not self.tool_name.strip():
            raise ValueError("ToolInvocationRequest.tool_name 不能为空")


class Tool(ABC):
    """一个可注册的工具."""

    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        """能力上界与机制参数. 同一实例每次返回同一个 spec (spec_hash 必须稳定)."""

    @abstractmethod
    def prepare(
        self, request: ToolInvocationRequest, context: ExecutionContext
    ) -> ToolPlan | PreparationError:
        """把入参规范化为计划. 只读, 无副作用, 使用 context 中已冻结的视图."""

    @abstractmethod
    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        """执行已授权的 effective_plan. 不重读原始入参, 不做任何安全判断."""
