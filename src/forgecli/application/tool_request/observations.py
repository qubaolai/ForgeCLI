"""回填模型的结构化 observation (ADR-0004 §8, ADR-0013 §2).

**策略结果不是 ToolResult.** policy_denied, approval_required 和
execution_environment_changed 都由协调器产出为 observation, 与工具执行结果分属不同的
类型空间 —— 混在一起, 模型就会把"被安全策略拒绝"当成"工具坏了"然后反复重试.

can_retry 是给模型的明确信号: Hard Deny 是 false (改写命令重试属于绕过尝试), 环境变化
是 true (重新裁决即可).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from forgecli.domain.agent.actions import (
    LoopObservation,
    ObservationDisposition,
    ObservationSource,
)
from forgecli.domain.security.findings import RiskFact
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.tool.result import ToolResult, ToolResultStatus

__all__ = ["ObservationKind", "ToolObservation", "rejected"]


class ObservationKind(Enum):
    TOOL_RESULT = "tool_result"
    # 工具成功了, 但它声明这次输出需要人裁决 (ADR-0023). 仍然是一次成功的调用 ——
    # 分成独立的 kind 只是为了让循环按字段分流, 不去读 content 里的文字.
    PLAN_REVIEW_REQUIRED = "plan_review_required"
    POLICY_DENIED = "policy_denied"
    # 需要重新审批 (批准后事实变化). 重试有意义.
    APPROVAL_REQUIRED = "approval_required"
    # 人类明确拒绝. 终止性的.
    APPROVAL_DENIED = "approval_denied"
    # 无人可裁决 (非交互环境 / EOF). 不是拒绝, 是没人回答 —— 重试永远还是这个结果.
    APPROVAL_UNAVAILABLE = "approval_unavailable"
    TOOL_UNAVAILABLE_IN_MODE = "tool_unavailable_in_mode"
    TOOL_UNAVAILABLE = "tool_unavailable"
    PREPARATION_FAILED = "preparation_failed"
    AUTHORIZATION_MISSING = "authorization_missing"
    EXECUTION_ENVIRONMENT_CHANGED = "execution_environment_changed"
    RECOVERY_UNAVAILABLE = "recovery_unavailable"
    OUTCOME_UNKNOWN = "outcome_unknown"

    @property
    def disposition(self) -> ObservationDisposition:
        """这条结论对后续工具调用意味着什么 (ADR-0013 §4 的人机边界).

        人类拒绝的是**意图**而不是那一条命令: 允许模型换个说法重试, 等于让它绕过人刚
        做的决定, 审批提示就成了摆设. 而策略拒绝说的是"这条路不通", 换条合法的路正是
        我们希望它做的 —— 所以两者分流, 判据是**谁说的不行**, 不是 mode.
        """
        if self is ObservationKind.PLAN_REVIEW_REQUIRED:
            return ObservationDisposition.AWAIT_USER_DECISION
        if self in _HALTING_KINDS:
            return ObservationDisposition.HALT
        if self in _BLOCKING_KINDS:
            return ObservationDisposition.BLOCKED
        return ObservationDisposition.CONTINUE

    @property
    def source(self) -> ObservationSource:
        if self in (
            ObservationKind.TOOL_RESULT,
            ObservationKind.PLAN_REVIEW_REQUIRED,
        ):
            return ObservationSource.TOOL
        if self in _SECURITY_KINDS:
            return ObservationSource.SECURITY
        return ObservationSource.ERROR


_SECURITY_KINDS = frozenset(
    {
        ObservationKind.POLICY_DENIED,
        ObservationKind.APPROVAL_REQUIRED,
        ObservationKind.APPROVAL_DENIED,
        ObservationKind.APPROVAL_UNAVAILABLE,
        ObservationKind.TOOL_UNAVAILABLE_IN_MODE,
        ObservationKind.RECOVERY_UNAVAILABLE,
    }
)

# 人做了决定, 或者根本没有人 —— 两种情况下继续派工具都没有意义.
_HALTING_KINDS = frozenset(
    {
        ObservationKind.APPROVAL_DENIED,
        ObservationKind.APPROVAL_UNAVAILABLE,
    }
)

# 机器说这条路不通. 换条路是合理的, 但要计数 —— 否则模型会一直换着花样撞墙.
_BLOCKING_KINDS = frozenset(
    {
        ObservationKind.POLICY_DENIED,
        ObservationKind.APPROVAL_REQUIRED,
        ObservationKind.RECOVERY_UNAVAILABLE,
        ObservationKind.AUTHORIZATION_MISSING,
    }
)


@dataclass(frozen=True)
class ToolObservation:
    """一次工具请求的最终结论. 无论成功, 被拒还是待审批, 都经这一个类型回填."""

    kind: ObservationKind
    message: str
    invocation_id: str
    tool_name: str
    reason_code: str = ""
    can_retry: bool = False
    plan_hash: str | None = None
    authorization_id: str | None = None
    approval_id: str | None = None
    checkpoint_id: str | None = None
    result: ToolResult | None = None
    risk_summary: tuple[str, ...] = field(default=())
    # 疑似被围栏拦下时附在结果正文后面的一段说明 (见 `fence_hint`). 空串表示没有疑似.
    #
    # 附加而不是替换: 命令自己的输出仍然是模型判断这次算不算成功的依据, 而这一段只是
    # 补上它读不出来的那一半 —— 那句 `Permission denied` 到底是谁说的.
    fence_hint: str = ""
    # 这个工具的结果正文进不进会话窗口 (ADR-0041 决策 6).
    # 取自 `ToolSpec.body_in_window`, 由协调器从注册表读一次填进来.
    #
    # 放在这里而不是让 ToolResult 自己带: 它是**工具的**策略, 不是这一次调用的事实.
    # 每个结果构造点都填一遍, 二十处里迟早有一处填反, 而填反不会报错 —— 只是窗口里
    # 突然多了或少了一份正文.
    body_in_window: bool = False

    @property
    def is_error(self) -> bool:
        if self.kind in (
            ObservationKind.TOOL_RESULT,
            ObservationKind.PLAN_REVIEW_REQUIRED,
        ):
            return self.result is not None and self.result.error is not None
        return True

    def to_loop_observation(self) -> LoopObservation:
        return LoopObservation(
            content=self.render(),
            source=self.kind.source,
            is_error=self.is_error,
            disposition=self.kind.disposition,
            # 只有真的跑出结果的调用才有来源身份. 被策略拒绝的那些没有内容可去重,
            # 也没有内容可降级.
            provenance=None if self.result is None else self.result.provenance,
            # 打转判定拿它比对 (见 BuiltinAgentLoop._track_progress): 正文移出窗口之后
            # content 变短, 更容易撞; 而句柄是内容寻址的, 正文不同则句柄不同.
            handle=(
                ""
                if self.result is None or self.result.provenance is None
                else self.result.provenance.artifact_id
            ),
        )

    def digest_line(self) -> str:
        """跨回合保留的一行结论 (ADR-0032 决策 7).

        回合之间只留这一行, 不留正文: 正文进跨轮历史等于把回合内溢出提前到第二轮.
        但"这件事做过, 结果是什么, 细节在哪"必须留下 —— 全丢的后果是下一轮的模型
        重新读一遍同样的文件, 重新 grep 一遍同样的词.

        由本类派生而不是在调用方手写: ADR-0031 §背景第 3 条记过一次手写副本的教训,
        那份形状照抄 render() 但不走它, 于是 render() 改了它不会跟着变.
        """
        if self.result is None:
            return self.kind.value
        head = _first_line(self.result.summary)
        parts = [head] if head else []
        if self.fence_hint:
            # 跨回合也要留下这个事实: 下一轮模型只看得到这一行, 而"上次是被围栏拦的"
            # 正是它决定下一步该做什么的依据.
            parts.append("(被围栏拦下)")
        if self.result.status is not ToolResultStatus.OK:
            parts.insert(0, self.result.status.value)
        provenance = self.result.provenance
        if provenance is not None and provenance.archived:
            parts.append(f"({provenance.artifact_id})")
        return " ".join(parts) if parts else self.kind.value

    def render(self) -> str:
        """给模型看的文本. 工具结果给三段形态, 其余给结构化拒绝说明.

        **这是正文进不进会话窗口的唯一闸门** (ADR-0041 决策 6). 下游全是字符串管道:
        LoopObservation.content -> ToolResultBlock -> provider 的 role=tool 消息.
        改窗口里装什么, 只需要改这一个函数.
        """
        if (
            self.kind
            in (ObservationKind.TOOL_RESULT, ObservationKind.PLAN_REVIEW_REQUIRED)
            and self.result is not None
        ):
            body = self.result.render_for_model(include_body=self.body_in_window)
            if self.fence_hint:
                return f"{body}\n\n{self.fence_hint}"
            return body
        lines = [f"[{self.kind.value}] {self.message}"]
        if self.reason_code:
            lines.append(f"reason: {self.reason_code}")
        lines.extend(f"risk: {item}" for item in self.risk_summary)
        lines.append(f"can_retry: {str(self.can_retry).lower()}")
        return "\n".join(lines)

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": self.kind.value,
            "invocation_id": self.invocation_id,
            "tool_name": self.tool_name,
            "message": self.message,
            "can_retry": self.can_retry,
        }
        if self.reason_code:
            payload["reason_code"] = self.reason_code
        if self.plan_hash:
            payload["plan_hash"] = self.plan_hash
        if self.authorization_id:
            payload["authorization_id"] = self.authorization_id
        if self.approval_id:
            payload["approval_id"] = self.approval_id
        if self.checkpoint_id:
            payload["checkpoint_id"] = self.checkpoint_id
        if self.result is not None:
            payload["result"] = self.result.to_payload()
        return payload


def rejected(
    kind: ObservationKind,
    message: str,
    *,
    invocation_id: str,
    tool_name: str,
    reason_code: str,
    can_retry: bool = False,
    plan: ToolPlan | None = None,
    approval_id: str | None = None,
    risk_facts: tuple[RiskFact, ...] = (),
) -> ToolObservation:
    """一次没有执行的调用的结论 (ADR-0028 规则 B).

    协调器里原本有五处几乎相同的 ToolObservation 构造, 每处都要记得从 plan 取
    plan_hash, 从 risk_facts 取 detail. 收成一个入口之后, "被拒绝的调用长什么样"
    只有一份定义 —— 而它正是模型唯一看得到的拒绝说明.
    """
    return ToolObservation(
        kind=kind,
        message=message,
        invocation_id=invocation_id,
        tool_name=tool_name,
        reason_code=reason_code,
        plan_hash=plan.plan_hash if plan is not None else None,
        approval_id=approval_id,
        risk_summary=tuple(fact.detail for fact in risk_facts),
        can_retry=can_retry,
    )


# 一行结论最多多长. 超了截断 —— 它要跨回合留在历史里, 而"跨回合的完整输出"正是
# ADR-0032 决策 7 要避免的东西.
_DIGEST_LIMIT = 160


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return (
                stripped
                if len(stripped) <= _DIGEST_LIMIT
                else stripped[:_DIGEST_LIMIT] + "..."
            )
    return ""
