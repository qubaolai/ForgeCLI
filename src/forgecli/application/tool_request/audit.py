"""工具调用的审计出口 (ADR-0004 §9 / §15, ADR-0013 §15).

`tool_requested` 是**写前事件**: 必须在执行之前落盘. 这个顺序是审计能回答"这条命令到底
跑没跑"的全部依据 —— 反过来写的话, 崩在执行中间与根本没执行过在日志上长得一模一样.

resume **不重放**任何东西. 会话恢复的语义是"回到之前的对话", 不是"回到之前的执行状态":
用户重新说一句要做什么就行, 工作区的实际状态模型下一轮读一遍就知道. 有 `tool_requested`
无 `tool_completed` 的调用因此只是一条"结果未知"的记录, 不触发任何自动动作.

两侧审计分工: 工具侧只记机制事实 (状态, 耗时, 退出码, 产物, 截断), 裁决理由与审批展示由
安全侧记录, 通过 `plan_hash` 与 `authorization_id` 关联, 不重复存同一份事实.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping

from forgecli.domain.security.decision import AuthorizationDecision
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.tool.result import ToolResult

__all__ = ["NullToolAudit", "ToolAuditSink"]


class ToolAuditSink(ABC):
    """审计写入口. 实现方决定落到 events.jsonl 还是别的地方."""

    @abstractmethod
    def bind_turn(self, turn_id: str) -> None:
        """把后续审计事件归属到这一轮.

        由 ToolRequestCoordinator 在每次 handle 入口调用. sink 是跨轮复用的长生命周期
        对象, 不绑定就会一直用构造时的值 —— 事件落盘时 turn_id 为空, 审计条目认不出属于
        哪一轮, resume 和事后追溯都对不上.
        """

    @abstractmethod
    def tool_requested(
        self, plan: ToolPlan, *, invocation_id: str, authorization_id: str
    ) -> None:
        """写前事件. 返回即表示已落盘, 之后才允许执行."""

    @abstractmethod
    def tool_completed(self, result: ToolResult, *, plan_hash: str) -> None: ...

    @abstractmethod
    def tool_rejected(
        self, tool_name: str, *, invocation_id: str, reason_code: str, message: str
    ) -> None:
        """没有执行就结束的调用也要留痕: 事后要能回答"模型请求过什么, 为什么没发生"."""

    @abstractmethod
    def policy_decision(
        self, decision: AuthorizationDecision, *, invocation_id: str
    ) -> None: ...

    @abstractmethod
    def approval_event(self, name: str, payload: Mapping[str, object]) -> None:
        """approval_requested / approval_resolved 等审批生命周期事件."""

    @abstractmethod
    def recovery_event(self, name: str, payload: Mapping[str, object]) -> None:
        """checkpoint_created / mutation_recorded / recovery_performed."""


class NullToolAudit(ToolAuditSink):
    """不落盘的默认实现.

    单元测试用它. 生产装配必须注入真实 sink —— 但注意审计**不是**安全控制:
    没有 sink 时链路照样拒绝该拒绝的请求, 只是少了事后追溯.
    """

    def bind_turn(self, turn_id: str) -> None:
        return None

    def tool_requested(
        self, plan: ToolPlan, *, invocation_id: str, authorization_id: str
    ) -> None:
        return None

    def tool_completed(self, result: ToolResult, *, plan_hash: str) -> None:
        return None

    def tool_rejected(
        self, tool_name: str, *, invocation_id: str, reason_code: str, message: str
    ) -> None:
        return None

    def policy_decision(
        self, decision: AuthorizationDecision, *, invocation_id: str
    ) -> None:
        return None

    def approval_event(self, name: str, payload: Mapping[str, object]) -> None:
        return None

    def recovery_event(self, name: str, payload: Mapping[str, object]) -> None:
        return None
