"""工具链路的**运行观察**出口 (ADR-0016 §4.3).

与 ToolAuditSink 分开是有意的, 不是重复造轮子:

- 审计 sink 写的是**恢复与追溯事实**, 落进 events.jsonl, 写前事件必须先落盘才允许执行.
- 运行观察写的是**给人看的时间线**, 活在进程内, 崩了就没了, 也不该有.

合成一个的后果是终端显示"开始执行"会被当成写前审计已落盘 —— 那正是 §4.3 点名要避免的.
两侧通过 turn_id / tool_call_id / invocation_id 关联, 但事件各自独立.

观察失败不能影响执行: 实现方自己吞掉异常, 或者交给事件总线隔离.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping

from forgecli.domain.security.decision import AuthorizationDecision
from forgecli.domain.security.vocabulary import ApprovalScope
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.tool.result import ToolResult

__all__ = ["NullToolRunObserver", "ToolRunObserver"]


class ToolRunObserver(ABC):
    """一次工具调用在管线里走到哪儿了. 只读通知, 不返回控制信号."""

    @abstractmethod
    def bind_turn(self, turn_id: str) -> None:
        """认领当前轮次. 与审计 sink 同理: 观察者跨轮复用, 不绑定就归不了属."""

    @abstractmethod
    def tool_prepared(
        self, plan: ToolPlan, *, invocation_id: str, arguments: Mapping[str, object]
    ) -> None: ...

    @abstractmethod
    def policy_resolved(
        self, decision: AuthorizationDecision, *, invocation_id: str
    ) -> None: ...

    @abstractmethod
    def approval_requested(
        self, tool_name: str, *, invocation_id: str, mandatory: bool, target_count: int
    ) -> None: ...

    @abstractmethod
    def approval_resolved(
        self,
        tool_name: str,
        *,
        invocation_id: str,
        outcome: str,
        scope: ApprovalScope | None = None,
    ) -> None: ...

    @abstractmethod
    def tool_started(self, tool_name: str, *, invocation_id: str) -> None: ...

    @abstractmethod
    def tool_completed(
        self, result: ToolResult, *, invocation_id: str, elapsed_ms: float
    ) -> None: ...

    @abstractmethod
    def tool_cancelled(
        self, tool_name: str, *, invocation_id: str, side_effect_unknown: bool
    ) -> None: ...


class NullToolRunObserver(ToolRunObserver):
    """不观察. 单元测试与无终端场景用它 —— 展示缺席不影响裁决与执行."""

    def bind_turn(self, turn_id: str) -> None:
        return None

    def tool_prepared(
        self, plan: ToolPlan, *, invocation_id: str, arguments: Mapping[str, object]
    ) -> None:
        return None

    def policy_resolved(
        self, decision: AuthorizationDecision, *, invocation_id: str
    ) -> None:
        return None

    def approval_requested(
        self, tool_name: str, *, invocation_id: str, mandatory: bool, target_count: int
    ) -> None:
        return None

    def approval_resolved(
        self,
        tool_name: str,
        *,
        invocation_id: str,
        outcome: str,
        scope: ApprovalScope | None = None,
    ) -> None:
        return None

    def tool_started(self, tool_name: str, *, invocation_id: str) -> None:
        return None

    def tool_completed(
        self, result: ToolResult, *, invocation_id: str, elapsed_ms: float
    ) -> None:
        return None

    def tool_cancelled(
        self, tool_name: str, *, invocation_id: str, side_effect_unknown: bool
    ) -> None:
        return None
