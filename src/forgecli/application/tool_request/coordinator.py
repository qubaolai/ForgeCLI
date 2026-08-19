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
from forgecli.application.tool_request.audit import NullToolAudit, ToolAuditSink
from forgecli.application.tool_request.catalog_predicates import catalog_query_for_mode
from forgecli.application.tool_request.observations import (
    ObservationKind,
    ToolObservation,
)
from forgecli.application.tool_request.run_observer import (
    ToolRunObserver,
)
from forgecli.application.tools.registry import ToolRegistry
from forgecli.application.tools.runtime import ToolRuntime
from forgecli.application.tools.tool import ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.agent.actions import ToolRequest
from forgecli.domain.recovery.mutation import Operation
from forgecli.domain.security.approval import (
    ApprovalBinding,
    ApprovalPresentation,
    ApprovalRequest,
    ApprovalResponse,
)
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.decision import AuthorizationDecision
from forgecli.domain.security.vocabulary import (
    ApprovalScope,
    Decision,
    DecisionReason,
)
from forgecli.domain.tool.authorization import (
    AuthorizationError,
    AuthorizationErrorCode,
)
from forgecli.domain.tool.catalog import ToolCatalog
from forgecli.domain.tool.errors import PreparationError
from forgecli.domain.tool.plan import (
    AnalysisSubject,
    ShellSubject,
    ToolPlan,
)
from forgecli.domain.tool.result import ToolResult, ToolResultStatus, TurnDisposition
from forgecli.shared.cancellation import CancelToken

__all__ = ["ToolRequestCoordinator"]

_ERROR_KINDS: dict[AuthorizationErrorCode, ObservationKind] = {
    AuthorizationErrorCode.AUTHORIZATION_MISSING: ObservationKind.AUTHORIZATION_MISSING,
    AuthorizationErrorCode.AUTHORIZATION_INVALID: ObservationKind.AUTHORIZATION_MISSING,
    AuthorizationErrorCode.EXECUTION_ENVIRONMENT_CHANGED: (
        ObservationKind.EXECUTION_ENVIRONMENT_CHANGED
    ),
    AuthorizationErrorCode.SPEC_CAPABILITY_VIOLATION: ObservationKind.POLICY_DENIED,
}


def _new_invocation_id() -> str:
    return f"inv_{uuid.uuid4().hex[:12]}"


def _new_approval_id() -> str:
    return f"apr_{uuid.uuid4().hex[:12]}"


def _raw_command_of(subject: AnalysisSubject | None) -> str | None:
    return subject.raw_command if isinstance(subject, ShellSubject) else None


def _shell_kind_of(subject: AnalysisSubject | None) -> str | None:
    return subject.shell_kind if isinstance(subject, ShellSubject) else None


def _unresolved_reason_of(decision: AuthorizationDecision) -> str | None:
    """目标集合为什么没封闭. 已封闭时返回 None.

    理由取风险事实里那几条谈目标与影响范围的. 界面不能只说"未封闭"就完事 —— 用户要判断
    的是"哪一部分说不清", 而那句话已经由分析器写好了.
    """
    if decision.effective_plan.target_resolution.closed:
        return None
    reasons = tuple(
        fact.detail
        for fact in decision.risk_facts
        if fact.code
        in ("target_resolution", "unproven_executable", "unresolved_target")
    )
    return "; ".join(reasons) if reasons else "分析无法确定完整目标集合"


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
        audit: ToolAuditSink | None = None,
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
        # 恢复层缺省为 None = 不建立恢复事务. 这不是"跳过安全检查": 需要恢复保障的写入
        # 会因为拿不到 recovery_binding 而在 _execute 里被挡下.
        self._mutations = mutations
        self._audit = audit or NullToolAudit()
        # 运行观察与审计分开: 前者给人看, 后者是恢复与追溯依据. 终端显示"开始执行"
        # 不等于写前审计已落盘 (ADR-0016 §4.3).
        self._observer = observer
        # 与 ToolAuthorizationService 共用同一个实例: 一边写规则一边查规则.
        self._learned = learned
        self._workspace_id = workspace_id
        self._new_invocation_id = invocation_id_factory
        self._new_approval_id = approval_id_factory
        self._clock = clock

    def catalog_for(self, policy: PolicyContext) -> ToolCatalog:
        """当前模式下模型可见的工具目录 (快照哈希进请求记录与审计)."""
        return self._registry.list(catalog_query_for_mode(policy.mode))

    def handle(
        self,
        request: ToolRequest,
        *,
        context: ExecutionContext,
        policy: PolicyContext,
        cancel: CancelToken | None = None,
    ) -> ToolObservation:
        # 审计 sink 跨轮复用, 每次进来先认领当前轮次: 漏了这一步, 落盘的工具审计事件
        # turn_id 就一直是构造时的空串, 事后无法把某次执行归到某一轮对话上.
        self._audit.bind_turn(policy.turn_id)
        self._observer.bind_turn(policy.turn_id)
        # 工作区身份由协调器持有, 补进 policy 供学习规则绑定 —— workspace 范围的规则
        # 不能跨项目命中.
        policy = replace(policy, workspace_id=self._workspace_id)
        invocation_id = self._new_invocation_id()
        catalog = self.catalog_for(policy)

        unavailable = self._check_availability(request, catalog, invocation_id, policy)
        if unavailable is not None:
            return unavailable

        prepared = self._prepare(request, invocation_id, context)
        if isinstance(prepared, ToolObservation):
            return prepared
        self._observer.tool_prepared(
            prepared, invocation_id=invocation_id, arguments=request.arguments
        )

        decision = self._authorization.evaluate(prepared, policy, context)
        self._audit.policy_decision(decision, invocation_id=invocation_id)
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
        return ToolObservation(
            kind=kind,
            message=message,
            invocation_id=invocation_id,
            tool_name=request.name,
            reason_code=kind.value,
            can_retry=False,
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
            return ToolObservation(
                kind=ObservationKind.PREPARATION_FAILED,
                message=outcome.message,
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
        return ToolObservation(
            kind=ObservationKind.PREPARATION_FAILED,
            message=(
                f"{plan.tool_name} 声明 target_declaration_ability={ability.value}, "
                f"却产出 target_resolution={plan.target_resolution.value}. "
                "这是工具实现与声明不一致, 已拒绝执行."
            ),
            invocation_id=invocation_id,
            tool_name=plan.tool_name,
            reason_code="target_declaration_violation",
            plan_hash=plan.plan_hash,
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
        binding = self._binding_of(decision.effective_plan, catalog, policy)
        presentation = self._presentation_of(decision, policy, context)
        approval = ApprovalRequest(
            approval_id=self._new_approval_id(),
            binding=binding,
            presentation=presentation,
            mandatory=decision.mandatory,
            risk_facts=tuple(fact.detail for fact in decision.risk_facts),
        )
        tool_name = decision.effective_plan.tool_name
        self._observer.approval_requested(
            tool_name,
            invocation_id=invocation_id,
            mandatory=decision.mandatory,
            target_count=len(decision.effective_plan.effects.mutating_targets),
        )
        response = self._approval.request(approval)
        self._observer.approval_resolved(
            tool_name,
            invocation_id=invocation_id,
            outcome=response.outcome.value,
            scope=response.scope if response.approved else None,
        )
        if not response.approved:
            return self._approval_not_granted(decision, response, invocation_id)

        revalidated = self._revalidate(
            request,
            binding,
            presentation.presentation_hash,
            invocation_id,
            context,
            policy,
            approval.approval_id,
        )
        if isinstance(revalidated, ToolObservation):
            return revalidated
        # 顺序: **重验通过之后**才落规则. 反过来的话, 一次因事实变化而作废的批准仍然会
        # 留下一条永久规则 —— 用户点同意时看到的那份视图已经不成立了, 而规则记的是它.
        # "用户确实点过同意"不等于"他同意的那件事仍然成立".
        if response.scope.learned and self._learned is not None:
            self._learned.record(revalidated, policy, response.scope)
        return replace(
            revalidated,
            decision=Decision.ALLOW,
            reason=DecisionReason.APPROVAL_GRANTED,
            message="人类已批准且全量重验通过",
        )

    def _scopes_for(self, decision: AuthorizationDecision) -> tuple[ApprovalScope, ...]:
        if self._learned is not None and self._learned.learnable(decision):
            return (ApprovalScope.ONCE, ApprovalScope.WORKSPACE)
        return (ApprovalScope.ONCE,)

    def _revalidate(
        self,
        request: ToolRequest,
        approved_binding: ApprovalBinding,
        approved_presentation_hash: str,
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

        current = self._binding_of(decision.effective_plan, catalog, policy)
        changed = approved_binding.differences(current)
        if changed:
            return ToolObservation(
                kind=ObservationKind.APPROVAL_REQUIRED,
                message="批准后事实已变化, 旧批准失效, 需要重新审批: "
                + ", ".join(changed),
                invocation_id=invocation_id,
                tool_name=request.name,
                reason_code="approval_binding_changed",
                plan_hash=decision.effective_plan.plan_hash,
                approval_id=approval_id,
                can_retry=True,
            )
        # 视图也要比对, 不能只比 ApprovalBinding.
        #
        # 两者覆盖的东西不同: binding 比的是 plan_hash / target_set_hash / 模式这类锚点,
        # 而用户实际读的是脚本正文, 写入内容, 完整目标清单和风险说明. 一份脚本文件在
        # 审批期间被改掉时, plan_hash 可能一点没变 (命令串没变), 但用户批准的那段代码
        # 已经不是将要执行的那段了.
        current_presentation = self._presentation_of(decision, policy, context)
        if current_presentation.presentation_hash != approved_presentation_hash:
            return ToolObservation(
                kind=ObservationKind.APPROVAL_REQUIRED,
                message=(
                    "批准后展示内容已变化 (脚本正文, 写入内容或目标清单), "
                    "需要重新审批"
                ),
                invocation_id=invocation_id,
                tool_name=request.name,
                reason_code="approval_presentation_changed",
                plan_hash=decision.effective_plan.plan_hash,
                approval_id=approval_id,
                can_retry=True,
            )
        return replace(decision, approval_presentation_hash=approved_presentation_hash)

    def _execute(
        self,
        decision: AuthorizationDecision,
        invocation_id: str,
        context: ExecutionContext,
        policy: PolicyContext,
        cancel: CancelToken | None,
    ) -> ToolObservation:
        # 顺序不能换: 先建立恢复保障, 再签发授权. 反过来就会出现"已经拿到执行授权,
        # 但还原不了"的窗口 (ADR-0015 §1).
        try:
            transaction = self._begin_recovery(decision, context, policy)
        except RecoveryUnavailableError as exc:
            return ToolObservation(
                kind=ObservationKind.RECOVERY_UNAVAILABLE,
                message=exc.message,
                invocation_id=invocation_id,
                tool_name=decision.effective_plan.tool_name,
                reason_code=DecisionReason.RECOVERY_UNAVAILABLE.value,
                plan_hash=decision.effective_plan.plan_hash,
                can_retry=False,
            )

        envelope = self._authorization.issue(
            decision,
            policy,
            recovery_binding=(
                transaction.checkpoint_id if transaction is not None else None
            ),
            # 走过 ASK 的请求把"人看到的那份视图"的哈希带进信封. 早先这里根本不传,
            # 于是 ExecutionAuthorization.approval_presentation_hash 恒为 None ——
            # 那个字段连同 ApprovalPresentation.presentation_hash 一起, 两头都没接.
            approval_presentation_hash=decision.approval_presentation_hash,
        )
        # 写前事件: 落盘之后才允许执行. 崩在执行中间时, 有 requested 无 completed 就是
        # "结果未知"的证据 —— 只作记录, resume 不据此重放.
        self._audit.tool_requested(
            decision.effective_plan,
            invocation_id=invocation_id,
            authorization_id=envelope.authorization_id,
        )
        # 顺序: 写前审计落盘之后才报"开始执行". 反过来的话终端会先于审计声称已开跑.
        tool_name = decision.effective_plan.tool_name
        self._observer.tool_started(tool_name, invocation_id=invocation_id)
        started = self._clock()
        try:
            result = self._runtime.execute(envelope, context, cancel=cancel)
        except AuthorizationError as exc:
            if transaction is not None:
                transaction.complete(failed=True)
            # 授权在执行入口被驳回, 进程没起来: 明确报"没产生副作用", 别让终端把它
            # 显示成"结果未知".
            self._observer.tool_cancelled(
                tool_name, invocation_id=invocation_id, side_effect_unknown=False
            )
            kind = _ERROR_KINDS[exc.code]
            return ToolObservation(
                kind=kind,
                message=exc.message,
                invocation_id=invocation_id,
                tool_name=decision.effective_plan.tool_name,
                reason_code=exc.code.value,
                plan_hash=decision.effective_plan.plan_hash,
                authorization_id=envelope.authorization_id,
                can_retry=exc.code
                is AuthorizationErrorCode.EXECUTION_ENVIRONMENT_CHANGED,
            )
        self._finish_recovery(transaction, decision, context, result)
        self._audit.tool_completed(result, plan_hash=decision.effective_plan.plan_hash)
        self._observer.tool_completed(
            result,
            invocation_id=invocation_id,
            elapsed_ms=(self._clock() - started) * 1000.0,
        )
        return ToolObservation(
            # 按**字段**置 kind, 不按工具名 (ADR-0023 决策 1). 协调器因此不需要认识
            # plan.write, 而机制对将来的 ask_user 一类工具同样成立.
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
        )

    def _begin_recovery(
        self,
        decision: AuthorizationDecision,
        context: ExecutionContext,
        policy: PolicyContext,
    ) -> MutationTransaction | None:
        """为真实工作区写入建立恢复事务, 并在屏障后保存 preimage."""
        plan = decision.effective_plan
        if self._mutations is None:
            if plan.mutates_workspace:
                # 需要恢复保障却没有恢复层: fail closed, 不能"先跑再说".
                raise RecoveryUnavailableError(
                    "该调用会修改真实工作区, 但当前没有可用的恢复层"
                )
            return None
        transaction = self._mutations.begin(
            plan,
            context,
            workspace_id=self._workspace_id,
            session_id=policy.session_id,
            turn_id=policy.turn_id,
            policy_version=policy.policy_version,
        )
        if transaction is None:
            return None
        for target in plan.effects.write_paths:
            transaction.record_write(target, Operation.OVERWRITE)
        for target in plan.effects.delete_paths:
            transaction.record_write(target, Operation.DELETE)
        for pair in plan.effects.move_pairs:
            transaction.record_write(pair.source, Operation.MOVE)
            transaction.record_write(pair.target, Operation.REPLACE)
        self._audit.recovery_event(
            "checkpoint_created", transaction.checkpoint.to_payload()
        )
        return transaction

    def _finish_recovery(
        self,
        transaction: MutationTransaction | None,
        decision: AuthorizationDecision,
        context: ExecutionContext,
        result: ToolResult,
    ) -> None:
        if transaction is None:
            return
        effects = decision.effective_plan.effects
        for target in effects.write_paths:
            transaction.record_result(target)
        for target in effects.delete_paths:
            transaction.record_result(target, deleted=True)
        checkpoint = transaction.complete(
            failed=result.status is not ToolResultStatus.OK
        )
        self._audit.recovery_event("mutation_recorded", checkpoint.to_payload())

    # ---- 小工具 ----

    def _binding_of(
        self, plan: ToolPlan, catalog: ToolCatalog, policy: PolicyContext
    ) -> ApprovalBinding:
        return ApprovalBinding.of(
            plan,
            catalog=catalog,
            execution_profile_hash=policy.execution_profile_hash,
            policy_version=policy.policy_version,
            mode=policy.mode,
        )

    def _presentation_of(
        self,
        decision: AuthorizationDecision,
        policy: PolicyContext,
        context: ExecutionContext,
    ) -> ApprovalPresentation:
        plan = decision.effective_plan
        effects = plan.effects
        summary = decision.message or decision.reason.value
        subject = plan.analysis_subject
        return ApprovalPresentation(
            action_summary=f"{plan.tool_name}: {summary}",
            user_intent_summary=policy.user_intent_summary,
            # 只有可沉淀成规则的请求才给用户 always 这个选项. 给了却学不到, 用户会以为
            # 自己授权过, 下次再被问时只会觉得系统坏了.
            allowed_scopes=self._scopes_for(decision),
            # 工作区目录进视图: 用户要看的第一件事是"这次动作能碰到哪些根目录".
            workspace_roots=context.workspace_roots,
            raw_command=_raw_command_of(subject),
            # 正文取**安全分析实际绑定的那几份快照**, 不是路径, 也不重新读一次文件
            # (重新读就是又开一个 TOCTOU 窗口). 早先这里要求 analysis_subject 是
            # ScriptSubject, 而没有任何工具产出 ScriptSubject —— 于是脚本正文恒为空,
            # `python deploy.py` 的审批界面只有一行命令.
            script_snapshots=decision.script_snapshots,
            content_previews=plan.content_previews,
            unresolved_reason=_unresolved_reason_of(decision),
            shell_kind=_shell_kind_of(subject),
            cwd=plan.execution_context.cwd,
            mode=policy.mode.value,
            read_paths=effects.read_paths,
            write_paths=effects.write_paths,
            delete_paths=effects.delete_paths,
            move_pairs=tuple((pair.source, pair.target) for pair in effects.move_pairs),
            target_set_hash=plan.target_set_hash,
            target_resolution=plan.target_resolution.value,
            network_targets=effects.network_targets,
            external_effects=effects.external_effects,
            risk_facts=tuple(fact.detail for fact in decision.risk_facts),
            invalidated_by=(
                "plan_hash",
                "target_set_hash",
                "catalog_snapshot_hash",
                "execution_profile_hash",
                "policy_version",
                "mode",
            ),
        )

    def _denied(
        self, decision: AuthorizationDecision, invocation_id: str
    ) -> ToolObservation:
        return ToolObservation(
            kind=ObservationKind.POLICY_DENIED,
            message=decision.message or "请求被安全策略拒绝",
            invocation_id=invocation_id,
            tool_name=decision.effective_plan.tool_name,
            reason_code=decision.reason.value,
            plan_hash=decision.effective_plan.plan_hash,
            risk_summary=tuple(fact.detail for fact in decision.risk_facts),
            can_retry=False,
        )

    def _approval_not_granted(
        self,
        decision: AuthorizationDecision,
        response: ApprovalResponse,
        invocation_id: str,
    ) -> ToolObservation:
        # 拒绝与"没人回答"是两回事: 前者是人做了决定, 后者是这个环境里没有人.
        # 混成一种, 非交互环境下模型会对着一个永远不会有人应答的提示反复重试.
        denied = response.outcome.value == "denied"
        return ToolObservation(
            kind=ObservationKind.APPROVAL_DENIED
            if denied
            else ObservationKind.APPROVAL_UNAVAILABLE,
            message=response.note
            or ("用户拒绝了本次请求" if denied else "当前环境无人可以审批"),
            invocation_id=invocation_id,
            tool_name=decision.effective_plan.tool_name,
            reason_code=decision.reason.value,
            plan_hash=decision.effective_plan.plan_hash,
            approval_id=response.approval_id,
            risk_summary=tuple(fact.detail for fact in decision.risk_facts),
            can_retry=False,
        )
