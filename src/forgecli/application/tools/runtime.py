"""ToolRuntime: 唯一带授权校验的执行入口 (ADR-0004 §2 / §6).

执行前的四道机械校验, 一道都不能跳:

1. 信封非空 —— 没有授权就没有执行, 不存在"未配置安全模块就直接跑"的默认分支.
2. 计划能力不超出 ToolSpec 上界 —— 超出即 spec_capability_violation, 不自动扩大 spec.
3. 信封未过期, 未撤销, 未被消费过 (默认一次性).
4. 执行画像与当前实例一致 —— 环境变了就重新裁决, 不在同一次授权里静默降级执行.

这些校验集中在这里, 而不是让每个工具各写一份: N 份实现必然出现 N 种偏差, 而漏写的
那一份就是旁路.

execute 只消费信封里的 effective_plan, 不重读原始入参 —— 这是消除"裁决的是一个语义,
执行的是另一个语义"的关键.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace

from forgecli.application.tools.registry import ToolRegistry, UnknownToolError
from forgecli.application.tools.tool import Tool
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.authorization import (
    AuthorizationError,
    AuthorizationErrorCode,
    ExecutionAuthorization,
)
from forgecli.domain.tool.hashing import digest_bytes
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.tool.result import (
    ToolError,
    ToolMetrics,
    ToolResult,
    ToolResultStatus,
)
from forgecli.shared.cancellation import CancelToken
from forgecli.shared.observability.log import get_log

__all__ = ["ToolRuntime"]

_log = get_log(__name__)


class ToolRuntime:
    """执行已授权的工具调用. 不认识 mode, 规则, 审批和分类器."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        clock: Callable[[], float] = time.time,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self._registry = registry
        self._clock = clock
        self._timer = timer
        # 已消费的一次性授权. 重试是新请求, 会产生新 plan_hash 与新授权.
        self._consumed: set[str] = set()

    def execute(
        self,
        authorization: ExecutionAuthorization | None,
        context: ExecutionContext,
        *,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        """执行一次调用.

        信封相关的问题抛 AuthorizationError (由协调器翻成 observation); 工具本身的失败
        归一为 ToolResult —— 前者是"不该执行", 后者是"执行了但没成功", 模型对这两件事
        的正确反应完全不同.
        """
        envelope = self._require_envelope(authorization)
        plan = envelope.effective_plan
        tool = self._resolve_tool(plan.tool_name)

        if tool.spec.exceeds_upper_bound(plan.capabilities):
            raise AuthorizationError(
                AuthorizationErrorCode.SPEC_CAPABILITY_VIOLATION,
                f"计划请求的能力超出 {plan.tool_name} 的声明上界",
            )
        if plan.spec_hash != tool.spec.spec_hash:
            raise AuthorizationError(
                AuthorizationErrorCode.EXECUTION_ENVIRONMENT_CHANGED,
                f"{plan.tool_name} 的 spec 已变化, 需要重新裁决",
            )
        envelope.ensure_usable(
            now_epoch=self._clock(),
            execution_profile_hash=context.execution_profile_hash,
        )
        if envelope.single_use:
            if envelope.authorization_id in self._consumed:
                raise AuthorizationError(
                    AuthorizationErrorCode.AUTHORIZATION_INVALID,
                    "一次性授权已被使用过",
                )
            # 一次性信封在首次执行尝试时即消费。即使随后发现文件或环境漂移，也必须重新
            # prepare/裁决，不能把旧信封留着等攻击者把状态换回去后再次使用。
            self._consumed.add(envelope.authorization_id)
        context_differences = context.differences(plan.execution_context)
        if context_differences:
            raise AuthorizationError(
                AuthorizationErrorCode.EXECUTION_ENVIRONMENT_CHANGED,
                "执行上下文在裁决后发生变化: " + ", ".join(context_differences),
            )
        self._verify_file_state(plan, context)

        invocation_id = plan.plan_id
        if cancel is not None and cancel.cancelled:
            _log.info("tool.cancelled_before_start", tool=plan.tool_name)
            return _cancelled_result(invocation_id, plan.tool_name)

        started = self._timer()
        try:
            result = tool.perform(plan, context, cancel)
        except Exception as exc:  # noqa: BLE001 - 工具异常必须归一, 不能穿透到循环
            # 归一成 ToolResult 之后, 异常对象与 traceback 就没人拿得到了 —— 模型只会
            # 看到一行 "tool_exception". 排查工具自身的 bug 全靠这里留下的 traceback.
            _log.exception(
                "tool.exception",
                tool=plan.tool_name,
                error=type(exc).__name__,
                message=str(exc),
                normalized_input=dict(plan.normalized_input),
            )
            return ToolResult(
                invocation_id=invocation_id,
                tool_name=plan.tool_name,
                status=ToolResultStatus.TOOL_ERROR,
                summary=f"{plan.tool_name} 抛异常: {type(exc).__name__}",
                data={"exception": type(exc).__name__},
                metrics=ToolMetrics(duration_seconds=self._timer() - started),
                error=ToolError(
                    code="tool_exception", message=str(exc) or type(exc).__name__
                ),
            )
        if result.metrics.duration_seconds == 0.0:
            # 工具没自报耗时时由运行时补上, 保证审计里总有这一项.
            return _with_duration(result, self._timer() - started)
        return result

    @staticmethod
    def _verify_file_state(plan: ToolPlan, context: ExecutionContext) -> None:
        for binding in plan.file_state_bindings:
            facts = context.filesystem.facts(binding.path)
            changed = (
                facts.realpath != binding.realpath
                or facts.file_identity != binding.file_identity
                or facts.size != binding.size
                or facts.mtime_ns != binding.mtime_ns
                or not facts.is_regular_file
            )
            if not changed:
                content = context.filesystem.read_bytes(
                    binding.realpath, max_bytes=binding.size + 1
                )
                changed = (
                    len(content) != binding.size
                    or digest_bytes(content) != binding.content_hash
                )
            if changed:
                _log.warning("tool.file_state_changed", path=binding.path)
                raise AuthorizationError(
                    AuthorizationErrorCode.EXECUTION_ENVIRONMENT_CHANGED,
                    f"安全分析读取的文件已变化，需要重新裁决: {binding.path}",
                )

    def _require_envelope(
        self, authorization: ExecutionAuthorization | None
    ) -> ExecutionAuthorization:
        if authorization is None:
            raise AuthorizationError(
                AuthorizationErrorCode.AUTHORIZATION_MISSING,
                "缺少执行授权信封",
            )
        return authorization

    def _resolve_tool(self, name: str) -> Tool:
        try:
            return self._registry.get(name)
        except UnknownToolError as exc:
            raise AuthorizationError(
                AuthorizationErrorCode.AUTHORIZATION_INVALID,
                f"授权指向未注册的工具: {name}",
            ) from exc


def _cancelled_result(invocation_id: str, tool_name: str) -> ToolResult:
    return ToolResult(
        invocation_id=invocation_id,
        tool_name=tool_name,
        status=ToolResultStatus.CANCELLED,
        summary=f"{tool_name} 在执行前已被取消",
        error=ToolError(code="cancelled", message="调用在执行前已被取消"),
    )


def _with_duration(result: ToolResult, duration: float) -> ToolResult:
    return replace(result, metrics=replace(result.metrics, duration_seconds=duration))
