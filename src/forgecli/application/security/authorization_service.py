"""ToolAuthorizationService: 所有工具共用的授权入口 (ADR-0004 §2, ADR-0013 §2).

它是**通用**入口, 不是 Shell 专用入口: 按 ToolPlan.capabilities 分派分析器, Shell 与
脚本分析只是其中两个能力分析器. 命名和接口都不得退化成只覆盖某一种工具.

服务只回答"是否允许", 不拥有恢复点, 不拥有沙箱策略, 也不拥有 Provider 生命周期. 三层
由 ToolRequestCoordinator 顺序装配.

evaluate 与 issue 是两步, 中间隔着人类审批和恢复绑定: 裁决 ALLOW 不等于拿到授权信封.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import replace

from forgecli.application.security.analyzers.registry import CapabilityAnalyzerRegistry
from forgecli.application.security.learned_rules import LearnedRuleService
from forgecli.application.security.policy_engine import PolicyEngine
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.decision import AuthorizationDecision
from forgecli.domain.security.vocabulary import Decision, DecisionReason
from forgecli.domain.tool.authorization import (
    ExecutionAuthorization,
    validate_narrowing,
)
from forgecli.domain.tool.plan import ToolPlan
from forgecli.shared.errors import ForgeError

__all__ = ["AuthorizationIssueError", "ToolAuthorizationService"]

_DEFAULT_TTL_SECONDS = 300.0


class AuthorizationIssueError(ForgeError):
    """在不该签发的时候要求签发授权. 装配错误, 直接抛给开发者."""


def _new_authorization_id() -> str:
    return f"auth_{uuid.uuid4().hex[:12]}"


class ToolAuthorizationService:
    """分析 -> 裁决 -> (审批与恢复之后) 签发一次性授权信封."""

    def __init__(
        self,
        analyzers: CapabilityAnalyzerRegistry,
        policy_engine: PolicyEngine,
        *,
        learned: LearnedRuleService | None = None,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] = _new_authorization_id,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
    ) -> None:
        self._analyzers = analyzers
        self._policy = policy_engine
        # 缺省不接: 没有学习规则时链路照常裁决, 只是 ASK 不会被抬成 ALLOW.
        self._learned = learned
        self._clock = clock
        self._new_id = id_factory
        self._ttl = ttl_seconds

    def evaluate(
        self,
        plan: ToolPlan,
        policy: PolicyContext,
        context: ExecutionContext,
    ) -> AuthorizationDecision:
        """按能力分派分析器, 再由本地策略引擎裁决.

        分析器只能收缩计划. 这里立刻校验一次, 而不是等到签发时 —— 一个把 UNKNOWN 目标
        "分析"成更大目标集合的分析器, 必须在它的结论被用于审批展示之前就被拦住.
        """
        findings = self._analyzers.analyze(plan, policy, context)
        validate_narrowing(plan, findings.plan)
        return self._apply_learned_rule(self._policy.decide(findings, policy), policy)

    def _apply_learned_rule(
        self, decision: AuthorizationDecision, policy: PolicyContext
    ) -> AuthorizationDecision:
        """命中学习规则的 ASK 抬成 ALLOW.

        顺序是关键: 查规则发生在策略引擎**出结论之后**, 而且只动 ASK. DENY 与
        Mandatory Ask 根本走不到这里 —— 所以"先学一条规则, 再拿它去过 Hard Deny"
        这条路不存在 (ADR-0013 §5.1, §12).
        """
        if self._learned is None:
            return decision
        if decision.decision is not Decision.ASK or decision.mandatory:
            return decision
        rule = self._learned.find(decision, policy)
        if rule is None:
            return decision
        return replace(
            decision,
            decision=Decision.ALLOW,
            reason=DecisionReason.LEARNED_ALLOW,
            matched_rule_id=rule.rule_id,
            message="命中此前在本工作区批准的学习规则",
        )

    def issue(
        self,
        decision: AuthorizationDecision,
        policy: PolicyContext,
        *,
        recovery_binding: str | None = None,
        approval_view_hash: str | None = None,
    ) -> ExecutionAuthorization:
        """签发一次性授权. 只接受 ALLOW 裁决.

        ASK 经人类批准后, 由协调器完成全量绑定重验并重新 evaluate, 拿到新的 ALLOW 裁决
        再来这里 —— 批准本身永远不是执行授权 (ADR-0004 §6.1).
        """
        if decision.decision is not Decision.ALLOW:
            raise AuthorizationIssueError(
                f"只有 ALLOW 裁决可以签发授权, 当前为 {decision.decision.value}"
            )
        now = self._clock()
        return ExecutionAuthorization(
            authorization_id=self._new_id(),
            effective_plan=decision.effective_plan,
            execution_profile_hash=policy.execution_profile_hash,
            issued_at_epoch=now,
            expires_at_epoch=now + self._ttl,
            single_use=True,
            recovery_binding=recovery_binding,
            approval_view_hash=approval_view_hash,
        )
