"""ToolRequestCoordinator: 工具, 安全, 恢复三层的唯一装配点 (ADR-0004 §2).

完整链路:

    能力门 -> describe -> prepare -> evaluate
      -> DENY  : policy_denied
      -> ASK   : 阻塞审批 -> 批准后**全量重验** -> 通过才继续
      -> ALLOW : (恢复绑定) -> issue -> ToolRuntime.execute -> tool_result

为什么重验不能省: ApprovalRequest 绑定的是"人类做决定那一刻"的事实快照. 批准之后到执行
之前, 计划, 目标集合, 目录, 策略版本和执行画像都可能变. 把变化后的事实静默附加到旧批准
上, 等于让用户批准了一件他没看过的事. 所以批准后要重新 prepare 与 evaluate, 逐项比对
ApprovalBinding; 任一项不同, 旧批准立即作废.

AgentLoop, slash command 和 resume 都必须经过这里 —— 任何绕过这条链路的执行路径都是
安全旁路, 不是"快捷方式".

**这里只留流程** (ADR-0028): 审批视图与绑定的内容由 approval_flow 构造, 恢复事务的
登记与收尾由 RecoveryFlow 负责. 分出去的是"拼什么"与"记什么", 留下的是"按什么顺序做".
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import replace

from forgecli.application.recovery.coordinator import (
    MutationTransaction,
    RecoveryUnavailableError,
    WorkspaceMutationCoordinator,
)
from forgecli.application.security.approval_service import (
    ApprovalService,
    PendingApprovalService,
)
from forgecli.application.security.authorization_service import ToolAuthorizationService
from forgecli.application.security.learned_rules import LearnedRuleService
from forgecli.application.tool_request.approval_flow import (
    build_binding,
    build_view,
    learn_block_reason,
    scopes_for,
)
from forgecli.application.tool_request.catalog_predicates import catalog_query_for_mode
from forgecli.application.tool_request.fence_hint import fence_hint
from forgecli.application.tool_request.observations import (
    ObservationKind,
    ToolObservation,
    rejected,
)
from forgecli.application.tool_request.recovery_flow import RecoveryFlow
from forgecli.application.tool_request.run_observer import ToolRunObserver
from forgecli.application.tools.registry import ToolRegistry
from forgecli.application.tools.runtime import ToolRuntime
from forgecli.application.tools.tool import ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.agent.actions import ToolRequest
from forgecli.domain.security.approval import ApprovalBinding, ApprovalRequest
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.decision import AuthorizationDecision
from forgecli.domain.security.vocabulary import Decision, DecisionReason
from forgecli.domain.tool.authorization import (
    AuthorizationError,
    AuthorizationErrorCode,
    ExecutionAuthorization,
)
from forgecli.domain.tool.catalog import ToolCatalog
from forgecli.domain.tool.errors import PreparationError
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.tool.result import ToolResult, TurnDisposition
from forgecli.shared.cancellation import CancelToken
from forgecli.shared.observability.context import bind
from forgecli.shared.observability.log import get_log

__all__ = ["ToolRequestCoordinator"]

_log = get_log(__name__)

_ERROR_KINDS: dict[AuthorizationErrorCode, ObservationKind] = {
    AuthorizationErrorCode.AUTHORIZATION_MISSING: ObservationKind.AUTHORIZATION_MISSING,
    AuthorizationErrorCode.AUTHORIZATION_INVALID: ObservationKind.AUTHORIZATION_MISSING,
    AuthorizationErrorCode.EXECUTION_ENVIRONMENT_CHANGED: (
        ObservationKind.EXECUTION_ENVIRONMENT_CHANGED
    ),
    AuthorizationErrorCode.SPEC_CAPABILITY_VIOLATION: ObservationKind.POLICY_DENIED,
}

# 这两类结论意味着工具真的跑过了, 终态事件由 _execute 负责; 其余一律没有执行.
_EXECUTED_KINDS = frozenset(
    {
        ObservationKind.TOOL_RESULT,
        ObservationKind.PLAN_REVIEW_REQUIRED,
        ObservationKind.OUTCOME_UNKNOWN,
    }
)


def _new_invocation_id() -> str:
    return f"inv_{uuid.uuid4().hex[:12]}"


def _new_approval_id() -> str:
    return f"apr_{uuid.uuid4().hex[:12]}"


class ToolRequestCoordinator:
    """把一次 ToolRequest 走完整条安全管线, 产出一个 ToolObservation."""

    def __init__(
        self,
        registry: ToolRegistry,
        runtime: ToolRuntime,
        authorization: ToolAuthorizationService,
        *,
        approval: ApprovalService | None = None,
        mutations: WorkspaceMutationCoordinator | None = None,
        observer: ToolRunObserver,
        learned: LearnedRuleService | None = None,
        workspace_id: str = "workspace",
        invocation_id_factory: Callable[[], str] = _new_invocation_id,
        approval_id_factory: Callable[[], str] = _new_approval_id,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._registry = registry
        self._runtime = runtime
        self._authorization = authorization
        # 默认 PendingApprovalService: 没接交互界面时 ASK 停在 pending, 不自动放行.
        self._approval = approval or PendingApprovalService()
        self._recovery = RecoveryFlow(mutations, workspace_id)
        self._observer = observer
        # 与 ToolAuthorizationService 共用同一个实例: 一边写规则一边查规则.
        self._learned = learned
        self._workspace_id = workspace_id
        self._new_invocation_id = invocation_id_factory
        self._new_approval_id = approval_id_factory
        self._clock = clock

    def catalog_for(self, policy: PolicyContext) -> ToolCatalog:
        """当前模式下模型可见的工具目录 (快照哈希进授权信封)."""
        return self._registry.list(catalog_query_for_mode(policy.mode))

    def handle(
        self,
        request: ToolRequest,
        *,
        context: ExecutionContext,
        policy: PolicyContext,
        cancel: CancelToken | None = None,
    ) -> ToolObservation:
        self._observer.bind_turn(policy.turn_id)
        # 工作区身份由协调器持有, 补进 policy 供学习规则绑定 —— workspace 范围的规则
        # 不能跨项目命中.
        policy = replace(policy, workspace_id=self._workspace_id)
        invocation_id = self._new_invocation_id()
        with bind(invocation_id=invocation_id, tool=request.name):
            return self._handle_bound(request, invocation_id, context, policy, cancel)

    def _handle_bound(
        self,
        request: ToolRequest,
        invocation_id: str,
        context: ExecutionContext,
        policy: PolicyContext,
        cancel: CancelToken | None,
    ) -> ToolObservation:
        """整条管线的日志都带 inv + tool: 一次调用从裁决到执行是连续的一段."""
        _log.info(
            "pipeline.start",
            arguments=request.arguments,
            mode=policy.mode.value,
            cwd=context.cwd,
            workspace_roots=list(context.workspace_roots),
        )
        with _log.span("pipeline") as span:
            observation = self._resolve(request, invocation_id, context, policy, cancel)
            span.set(
                kind=observation.kind.value,
                reason_code=observation.reason_code,
                is_error=observation.is_error,
            )
        if observation.kind not in _EXECUTED_KINDS:
            _log.info(
                "pipeline.rejected",
                kind=observation.kind.value,
                reason_code=observation.reason_code,
                message=observation.message,
            )
            # 没有执行的调用也必须留下终态. 少了这一步, 展示层会永远停在"未完成",
            # events.jsonl 里也查不到这次请求发生过 —— 而模型其实早就拿到了结论.
            self._observer.tool_rejected(
                observation.tool_name,
                invocation_id=invocation_id,
                reason_code=observation.reason_code,
                message=observation.message,
            )
        return observation

    def _resolve(
        self,
        request: ToolRequest,
        invocation_id: str,
        context: ExecutionContext,
        policy: PolicyContext,
        cancel: CancelToken | None,
    ) -> ToolObservation:
        catalog = self.catalog_for(policy)

        unavailable = self._check_availability(request, catalog, invocation_id, policy)
        if unavailable is not None:
            return unavailable

        # 这两段用手工计时而不是 log.span: span 会各自多打一行只带 elapsed_ms 的
        # `.ok`, 而它们紧接着就是 pipeline.prepared / pipeline.decision —— 同一件事
        # 打两行, 一次工具密集的对话里这就是几百行纯噪音. 耗时折进那两行里, 指标照记.
        started = time.perf_counter()
        prepared = self._prepare(request, invocation_id, context)
        prepare_ms = (time.perf_counter() - started) * 1000.0
        if isinstance(prepared, ToolObservation):
            _log.warning(
                "pipeline.prepare_failed",
                kind=prepared.kind.value,
                message=prepared.message,
            )
            return prepared
        _log.info(
            "pipeline.prepared",
            plan_hash=prepared.plan_hash,
            capabilities=sorted(item.value for item in prepared.capabilities),
            read_paths=list(prepared.effects.read_paths),
            write_paths=list(prepared.effects.write_paths),
            delete_paths=list(prepared.effects.delete_paths),
            network_targets=list(prepared.effects.network_targets),
            child_process=prepared.effects.child_process,
            target_resolution=prepared.target_resolution.value,
            normalized_input=dict(prepared.normalized_input),
            elapsed_ms=prepare_ms,
        )
        self._observer.tool_prepared(
            prepared, invocation_id=invocation_id, arguments=request.arguments
        )

        started = time.perf_counter()
        decision = self._authorization.evaluate(prepared, policy, context)
        evaluate_ms = (time.perf_counter() - started) * 1000.0
        _log.info(
            "pipeline.decision",
            decision=decision.decision.value,
            reason=decision.reason.value,
            matched_rule=decision.matched_rule_id,
            mandatory=decision.mandatory,
            message=decision.message,
            risk_facts=[fact.code for fact in decision.risk_facts],
            executables=list(decision.executable_names),
            elapsed_ms=evaluate_ms,
        )
        self._observer.policy_resolved(decision, invocation_id=invocation_id)
        if decision.decision is Decision.DENY:
            return self._denied(decision, invocation_id)
        if decision.decision is Decision.ASK:
            outcome = self._seek_approval(
                decision, request, catalog, invocation_id, context, policy
            )
            if isinstance(outcome, ToolObservation):
                return outcome
            decision = outcome

        return self._execute(decision, invocation_id, context, policy, cancel)

    # ---- 各段 ----

    def _check_availability(
        self,
        request: ToolRequest,
        catalog: ToolCatalog,
        invocation_id: str,
        policy: PolicyContext,
    ) -> ToolObservation | None:
        """目录外的请求在这里被挡下. 这是兜底, 不是能力门本身."""
        if catalog.contains(request.name):
            return None
        registered = self._registry.contains(request.name)
        _log.warning(
            "pipeline.unavailable",
            registered=registered,
            mode=policy.mode.value,
            catalog=[entry.name for entry in catalog.entries],
        )
        kind = (
            ObservationKind.TOOL_UNAVAILABLE_IN_MODE
            if registered
            else ObservationKind.TOOL_UNAVAILABLE
        )
        message = (
            f"工具 {request.name} 在 {policy.mode.value} 模式下不可用"
            if registered
            else f"工具 {request.name} 未注册"
        )
        return rejected(
            kind,
            message,
            invocation_id=invocation_id,
            tool_name=request.name,
            reason_code=kind.value,
        )

    def _prepare(
        self,
        request: ToolRequest,
        invocation_id: str,
        context: ExecutionContext,
    ) -> ToolPlan | ToolObservation:
        tool = self._registry.get(request.name)
        invocation = ToolInvocationRequest(
            invocation_id=invocation_id,
            tool_name=request.name,
            arguments=request.arguments,
            tool_call_id=request.tool_call_id,
        )
        outcome = tool.prepare(invocation, context)
        if isinstance(outcome, ToolPlan):
            violated = self._check_declaration(outcome, invocation_id)
            if violated is not None:
                return violated
        if isinstance(outcome, PreparationError):
            # prepare 失败直接回填模型, 不进入安全裁决: 裁决一个不成立的计划没有意义.
            return rejected(
                ObservationKind.PREPARATION_FAILED,
                outcome.message,
                invocation_id=invocation_id,
                tool_name=request.name,
                reason_code=outcome.code.value,
                can_retry=True,
            )
        return outcome

    def _check_declaration(
        self, plan: ToolPlan, invocation_id: str
    ) -> ToolObservation | None:
        """计划的目标封闭程度不得弱于 spec 声明的上界 (ADR-0004 §4).

        声明 STATIC 等于承诺"我每次都能从入参算出确定的目标集合". 兑现不了却放行的话,
        下游会按"目标已封闭"走快速裁决 —— 一个实际上没封闭的调用因此拿到普通 ALLOW.
        这是工具实现的 bug, 不是模型的输入问题, 所以在这里 fail closed 而不是重试.
        """
        ability = self._registry.describe(plan.tool_name).target_declaration_ability
        if ability.permits(plan.target_resolution):
            return None
        return rejected(
            ObservationKind.PREPARATION_FAILED,
            f"{plan.tool_name} 声明 target_declaration_ability={ability.value}, "
            f"却产出 target_resolution={plan.target_resolution.value}. "
            "这是工具实现与声明不一致, 已拒绝执行.",
            invocation_id=invocation_id,
            tool_name=plan.tool_name,
            reason_code="target_declaration_violation",
            plan=plan,
            # 重试没有意义: 同一份代码会给出同样的结果.
            can_retry=False,
        )

    def _seek_approval(
        self,
        decision: AuthorizationDecision,
        request: ToolRequest,
        catalog: ToolCatalog,
        invocation_id: str,
        context: ExecutionContext,
        policy: PolicyContext,
    ) -> AuthorizationDecision | ToolObservation:
        """阻塞等待人类决定, 批准后做全量绑定重验."""
        view = build_view(
            decision,
            policy,
            context,
            allowed_scopes=scopes_for(decision, self._learned),
            learn_blocked_reason=learn_block_reason(decision, self._learned),
        )
        binding = build_binding(
            decision.effective_plan, catalog, policy, view.view_hash
        )
        approval = ApprovalRequest(
            approval_id=self._new_approval_id(),
            binding=binding,
            view=view,
            mandatory=decision.mandatory,
        )
        tool_name = decision.effective_plan.tool_name
        self._observer.approval_requested(
            tool_name,
            invocation_id=invocation_id,
            mandatory=decision.mandatory,
            target_count=len(decision.effective_plan.effects.mutating_targets),
        )
        _log.info(
            "approval.requested",
            approval_id=approval.approval_id,
            mandatory=decision.mandatory,
            reason=decision.reason.value,
            mutating_targets=list(decision.effective_plan.effects.mutating_targets),
            allowed_scopes=[scope.value for scope in view.allowed_scopes],
        )
        with _log.span("approval.wait", approval_id=approval.approval_id) as span:
            response = self._approval.request(approval)
            span.set(outcome=response.outcome.value, approved=response.approved)
        _log.info(
            "approval.resolved",
            approval_id=response.approval_id,
            outcome=response.outcome.value,
            approved=response.approved,
            scope=response.scope.value if response.approved else None,
            learned=response.scope.learned if response.approved else False,
            note=response.note,
        )
        self._observer.approval_resolved(
            tool_name,
            invocation_id=invocation_id,
            outcome=response.outcome.value,
            scope=response.scope if response.approved else None,
        )
        response_error = approval.response_error(response)
        if response_error is not None:
            return rejected(
                ObservationKind.APPROVAL_UNAVAILABLE,
                response_error,
                invocation_id=invocation_id,
                tool_name=tool_name,
                reason_code="approval_response_invalid",
                plan=decision.effective_plan,
                approval_id=approval.approval_id,
            )
        if not response.approved:
            # 拒绝与"没人回答"是两回事: 前者是人做了决定, 后者是这个环境里没有人.
            # 混成一种, 非交互环境下模型会对着一个永远不会有人应答的提示反复重试.
            denied = response.outcome.value == "denied"
            return rejected(
                ObservationKind.APPROVAL_DENIED
                if denied
                else ObservationKind.APPROVAL_UNAVAILABLE,
                response.note
                or ("用户拒绝了本次请求" if denied else "当前环境无人可以审批"),
                invocation_id=invocation_id,
                tool_name=tool_name,
                reason_code=decision.reason.value,
                plan=decision.effective_plan,
                approval_id=response.approval_id,
                risk_facts=decision.risk_facts,
            )

        revalidated = self._revalidate(
            request, binding, invocation_id, context, policy, approval.approval_id
        )
        if isinstance(revalidated, ToolObservation):
            return revalidated
        # 顺序: **重验通过之后**才落规则. 反过来的话, 一次因事实变化而作废的批准仍然会
        # 留下一条永久规则 —— 用户点同意时看到的那份视图已经不成立了, 而规则记的是它.
        # "用户确实点过同意"不等于"他同意的那件事仍然成立".
        if response.scope.learned and self._learned is not None:
            self._learned.record(revalidated, policy, response.scope)
            _log.info(
                "approval.rule_learned",
                scope=response.scope.value,
                tool=revalidated.effective_plan.tool_name,
            )
        return replace(
            revalidated,
            decision=Decision.ALLOW,
            reason=DecisionReason.APPROVAL_GRANTED,
            message="人类已批准且全量重验通过",
        )

    def _revalidate(
        self,
        request: ToolRequest,
        approved_binding: ApprovalBinding,
        invocation_id: str,
        context: ExecutionContext,
        policy: PolicyContext,
        approval_id: str,
    ) -> AuthorizationDecision | ToolObservation:
        """重新 prepare 与 evaluate, 再逐项比对绑定事实 (ADR-0004 §6.1)."""
        catalog = self.catalog_for(policy)
        prepared = self._prepare(request, invocation_id, context)
        if isinstance(prepared, ToolObservation):
            return prepared

        decision = self._authorization.evaluate(prepared, policy, context)
        if decision.decision is Decision.DENY:
            # 批准不能覆盖 Hard Deny: 用户刚点了同意也一样.
            return self._denied(decision, invocation_id)

        current_view = build_view(
            decision,
            policy,
            context,
            allowed_scopes=scopes_for(decision, self._learned),
            learn_blocked_reason=learn_block_reason(decision, self._learned),
        )
        current = build_binding(
            decision.effective_plan, catalog, policy, current_view.view_hash
        )
        changed = approved_binding.differences(current)
        if changed:
            _log.warning(
                "approval.binding_changed",
                approval_id=approval_id,
                changed=list(changed),
            )
            return rejected(
                ObservationKind.APPROVAL_REQUIRED,
                "批准后事实已变化, 旧批准失效, 需要重新审批: " + ", ".join(changed),
                invocation_id=invocation_id,
                tool_name=request.name,
                reason_code="approval_binding_changed",
                plan=decision.effective_plan,
                approval_id=approval_id,
                can_retry=True,
            )
        return replace(decision, approval_view_hash=approved_binding.view_hash)

    def _execute(
        self,
        decision: AuthorizationDecision,
        invocation_id: str,
        context: ExecutionContext,
        policy: PolicyContext,
        cancel: CancelToken | None,
    ) -> ToolObservation:
        plan = decision.effective_plan
        # 顺序不能换: 先建立恢复保障, 再签发授权. 反过来就会出现"已经拿到执行授权,
        # 但还原不了"的窗口 (ADR-0015 §1).
        try:
            with _log.span("recovery.begin", mutates=plan.mutates_workspace) as span:
                transaction = self._recovery.begin(plan, context, policy)
                span.set(
                    checkpoint_id=(
                        None if transaction is None else transaction.checkpoint_id
                    )
                )
        except RecoveryUnavailableError as exc:
            _log.error("recovery.unavailable", message=exc.message)
            return rejected(
                ObservationKind.RECOVERY_UNAVAILABLE,
                exc.message,
                invocation_id=invocation_id,
                tool_name=plan.tool_name,
                reason_code=DecisionReason.RECOVERY_UNAVAILABLE.value,
                plan=plan,
            )

        envelope = self._authorization.issue(
            decision,
            policy,
            recovery_binding=(
                transaction.checkpoint_id if transaction is not None else None
            ),
            # 走过 ASK 的请求把"人看到的那份视图"的哈希带进授权,
            # 让"批准的对象"与"执行的对象"之间留下一条可核对的链.
            approval_view_hash=decision.approval_view_hash,
        )
        self._observer.tool_started(plan.tool_name, invocation_id=invocation_id)
        started = self._clock()
        try:
            with _log.span(
                "tool.execute",
                authorization_id=envelope.authorization_id,
                plan_hash=plan.plan_hash,
            ) as span:
                result = self._runtime.execute(envelope, context, cancel=cancel)
                span.set(
                    status=result.status.value,
                    exit_code=result.metrics.exit_code,
                    bytes_out=result.metrics.bytes_out,
                    workspace_mutated=result.workspace_mutated,
                    artifacts=[item.artifact_id for item in result.artifacts],
                )
        except AuthorizationError as exc:
            _log.error(
                "tool.authorization_refused",
                code=exc.code.value,
                message=exc.message,
            )
            return self._authorization_refused(
                exc, transaction, plan, invocation_id, envelope.authorization_id
            )
        if result.error is not None:
            _log.warning(
                "tool.failed",
                status=result.status.value,
                code=result.error.code,
                message=result.error.message,
            )
        # 工具回填给模型的正文原样进 debug: 上一层只记了字节数, 而"模型看到的那段
        # 输出到底长什么样"是排查"它为什么这么理解"的唯一依据.
        #
        # 先问再取: result.text 会把所有内容块拼成一整段, 一次大文件读取就是几 MB,
        # 而参数在调用之前就求值了.
        if _log.enabled_for_debug():
            _log.debug("tool.output", text=result.raw_output())
        self._recovery.finish(transaction, plan, result)
        self._observer.tool_completed(
            result,
            invocation_id=invocation_id,
            elapsed_ms=(self._clock() - started) * 1000.0,
        )
        return self._completed(
            result,
            decision,
            invocation_id,
            transaction,
            envelope,
            # 只在有围栏时才算: 没围栏的话那句 Permission denied 只能是文件系统
            # 本身的权限, 说成围栏就是在骗模型.
            # 围栏扫的是命令**实际输出**里有没有越界痕迹, 给它摘要等于把这道
            # 防线关掉 (ADR-0041 决策 9).
            fence_hint(result.raw_output(), policy.fence, confined=policy.confined),
        )

    def _authorization_refused(
        self,
        exc: AuthorizationError,
        transaction: MutationTransaction | None,
        plan: ToolPlan,
        invocation_id: str,
        authorization_id: str,
    ) -> ToolObservation:
        self._recovery.abort(transaction)
        # 授权在执行入口被驳回, 进程没起来: 明确报"没产生副作用", 别让终端把它
        # 显示成"结果未知".
        self._observer.tool_cancelled(
            plan.tool_name, invocation_id=invocation_id, side_effect_unknown=False
        )
        return replace(
            rejected(
                _ERROR_KINDS[exc.code],
                exc.message,
                invocation_id=invocation_id,
                tool_name=plan.tool_name,
                reason_code=exc.code.value,
                plan=plan,
                can_retry=(
                    exc.code is AuthorizationErrorCode.EXECUTION_ENVIRONMENT_CHANGED
                ),
            ),
            authorization_id=authorization_id,
        )

    def _completed(
        self,
        result: ToolResult,
        decision: AuthorizationDecision,
        invocation_id: str,
        transaction: MutationTransaction | None,
        envelope: ExecutionAuthorization,
        hint: str = "",
    ) -> ToolObservation:
        return ToolObservation(
            # 按**字段**置 kind, 不按工具名 (ADR-0023 决策 1). 协调器因此不需要认识
            # plan_write, 而机制对将来的 ask_user 一类工具同样成立.
            kind=(
                ObservationKind.PLAN_REVIEW_REQUIRED
                if result.turn_disposition is TurnDisposition.AWAIT_USER_DECISION
                else ObservationKind.TOOL_RESULT
            ),
            message=result.status.value,
            invocation_id=invocation_id,
            tool_name=result.tool_name,
            reason_code=decision.reason.value,
            plan_hash=decision.effective_plan.plan_hash,
            authorization_id=envelope.authorization_id,
            checkpoint_id=(
                transaction.checkpoint_id if transaction is not None else None
            ),
            result=result,
            fence_hint=hint,
            body_in_window=self._body_in_window(result.tool_name),
        )

    def _body_in_window(self, tool_name: str) -> bool:
        """这个工具的正文进不进窗口 (ADR-0041 决策 6).

        先问 `contains` 再取: `describe` 对未注册的名字抛 UnknownToolError, 而这一步跑在
        工具**已经执行完**之后 —— 事务已 finish. 让它在这里抛, 等于把一次
        已经产生真实写入的调用变成一个异常, 而恢复点已经过去了.

        认不出名字时按 False: 少给正文只是多一次取回, 而把不该进窗口的正文放进去是
        每一轮都要付的账.
        """
        if not self._registry.contains(tool_name):
            _log.warning("tool.spec_missing", tool=tool_name)
            return False
        return self._registry.describe(tool_name).body_in_window

    def _denied(
        self, decision: AuthorizationDecision, invocation_id: str
    ) -> ToolObservation:
        # "跑不了"与"不许跑"分流. 两者都不执行, 但模型该做的下一步完全相反: 前者要换一条
        # 命令, 后者换写法就是绕过尝试. 混成一种时模型拿到的是 can_retry: false 加一个
        # 它无从下手的理由码 —— 于是它要么原地重试, 要么把一次环境问题当成被禁止.
        unrunnable = decision.unrunnable
        if unrunnable is not None:
            _log.warning(
                "pipeline.unrunnable",
                reason=unrunnable.value,
                message=decision.message,
                risk_facts=[fact.code for fact in decision.risk_facts],
            )
            return rejected(
                ObservationKind.COMMAND_UNRUNNABLE,
                decision.message or "这条命令在当前环境下接不起来",
                invocation_id=invocation_id,
                tool_name=decision.effective_plan.tool_name,
                reason_code=unrunnable.value,
                plan=decision.effective_plan,
                risk_facts=decision.risk_facts,
                # 换一条命令是有意义的: 这里拒的是"我们没接起来", 不是"你不该做这件事".
                can_retry=True,
            )
        _log.warning(
            "pipeline.denied",
            reason=decision.reason.value,
            message=decision.message,
            risk_facts=[fact.code for fact in decision.risk_facts],
        )
        return rejected(
            ObservationKind.POLICY_DENIED,
            decision.message or "请求被安全策略拒绝",
            invocation_id=invocation_id,
            tool_name=decision.effective_plan.tool_name,
            reason_code=decision.reason.value,
            plan=decision.effective_plan,
            risk_facts=decision.risk_facts,
        )
