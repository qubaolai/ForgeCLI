"""memory_write / memory_forget: 跨会话记忆的写入 (ADR-0033 决策 9).

走工具管线的收益是**全套机制免费继承**: TOOL_REQUESTED / TOOL_COMPLETED 审计, 运行
事件, 目录谓词, mode 门 —— 不用为记忆再造一套.

形状照 planning_tools 抄, 连同它那条理由: `additionalProperties: False` 加上没有路径
字段, 构成这批工具目标集合在机制上封闭的证明. 写入位置由 scope 与 project_id 算出,
模型指定不了.

**不弹审批** (决策 2): 记忆的全部价值在于无感积累, 一个每次都要人点确认的记忆系统,
用户会在第三次的时候关掉它. 承重的缓解不是这道闸, 是决策 3 那条"记忆在任何情况下都
不参与安全裁决" —— 最坏情况是模型照着一条被污染的记忆去请求一个动作, 然后照常撞上
ADR-0013 / ADR-0020 / ADR-0027 那整条链.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from forgecli.application.memory.memory_service import (
    MemoryRejection,
    MemoryService,
)
from forgecli.application.memory.memory_store import (
    MAX_ENTRIES_PER_SCOPE,
    MAX_VALUE_BYTES,
)
from forgecli.application.tools.builtin.base import validate_arguments
from forgecli.application.tools.tool import Tool, ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.memory.entry import MemoryScope
from forgecli.domain.prompt import text as prompt_text
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.errors import PreparationError, PreparationErrorCode
from forgecli.domain.tool.plan import (
    DeclarationConfidence,
    PlanEffects,
    TargetResolution,
    ToolPlan,
    WorkspaceScope,
)
from forgecli.domain.tool.result import (
    ContentPart,
    ToolError,
    ToolResult,
    ToolResultStatus,
)
from forgecli.domain.tool.spec import TargetDeclarationAbility, ToolSpec
from forgecli.shared.cancellation import CancelToken

__all__ = ["MemoryForgetTool", "MemoryWriteTool"]

_SCOPE_DESCRIPTION = (
    "project = 这个项目的事实 (测试命令, 构建命令, 目录含义, 架构事实); "
    "user = 跟着你走的偏好 (语言, 输出风格)."
)

# 拒绝原因 -> 给模型的说明. 映射住在这里而不是 text.py: MemoryRejection 是 application
# 的类型, 而 domain 不能 import application (ADR-0002 分层方向).
_REJECTION_TEXT: dict[MemoryRejection, str] = {
    MemoryRejection.INVALID_KEY: prompt_text.MEMORY_REJECT_INVALID_KEY,
    MemoryRejection.EMPTY_VALUE: prompt_text.MEMORY_REJECT_EMPTY_VALUE,
    MemoryRejection.VALUE_TOO_LONG: prompt_text.MEMORY_REJECT_VALUE_TOO_LONG.format(
        limit=MAX_VALUE_BYTES
    ),
    MemoryRejection.LOOKS_LIKE_SECRET: prompt_text.MEMORY_REJECT_SECRET,
    MemoryRejection.SCOPE_FULL: prompt_text.MEMORY_REJECT_SCOPE_FULL.format(
        limit=MAX_ENTRIES_PER_SCOPE
    ),
}


def _spec(
    name: str,
    title: str,
    description: str,
    properties: Mapping[str, object],
    required: tuple[str, ...],
) -> ToolSpec:
    return ToolSpec(
        name=name,
        version="1",
        title=title,
        description=description,
        input_schema={
            "type": "object",
            "properties": dict(properties),
            "required": list(required),
            # 没有路径字段, 且不许多带 —— 见模块注释里那条"机制上封闭"的证明.
            "additionalProperties": False,
        },
        output_schema={"type": "object", "properties": {"body": {"type": "string"}}},
        declared_capabilities=frozenset({Capability.MEMORY_WRITE}),
        target_declaration_ability=TargetDeclarationAbility.STATIC,
        default_timeout_seconds=5.0,
    )


class _MemoryTool(Tool):
    """共用 prepare: 目标集合恒为空, 因为写哪由 scope 算出, 模型指定不了."""

    _SPEC: ToolSpec

    def __init__(self, memory: MemoryService) -> None:
        self._memory = memory

    @property
    def spec(self) -> ToolSpec:
        return self._SPEC

    def prepare(
        self, request: ToolInvocationRequest, context: ExecutionContext
    ) -> ToolPlan | PreparationError:
        invalid = validate_arguments(self._SPEC, request.arguments)
        if invalid is not None:
            return invalid
        raw_scope = str(request.arguments.get("scope", ""))
        if raw_scope not in {scope.value for scope in MemoryScope}:
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message=_SCOPE_DESCRIPTION,
                field_path="scope",
            )
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=self._SPEC.name,
            spec_hash=self._SPEC.spec_hash,
            normalized_input=MappingProxyType(dict(request.arguments)),
            capabilities=frozenset({Capability.MEMORY_WRITE}),
            # 空 effects 是事实, 不是省略: 记忆落在 ~/.forge/ 下, 碰不到工作区.
            effects=PlanEffects(),
            target_resolution=TargetResolution.STATIC,
            workspace_scope=WorkspaceScope.IN_WORKSPACE,
            execution_context=context.to_ref(),
            declaration_confidence=DeclarationConfidence.DECLARED,
        )

    def _scope_of(self, plan: ToolPlan) -> MemoryScope:
        return MemoryScope(str(plan.normalized_input["scope"]))

    def _ok(self, plan: ToolPlan, body: str) -> ToolResult:
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=self._SPEC.name,
            status=ToolResultStatus.OK,
            content_parts=(ContentPart(text=body),),
        )

    def _refused(self, plan: ToolPlan, rejection: MemoryRejection) -> ToolResult:
        """被拒必须说清原因. 静默截断或含糊的失败会让模型再也不碰这个工具."""
        message = _REJECTION_TEXT[rejection]
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=self._SPEC.name,
            status=ToolResultStatus.INVALID_INPUT,
            content_parts=(ContentPart(text=message),),
            error=ToolError(
                code=rejection.value,
                message=message,
                # 换个内容重试是有意义的, 但换个说法绕开凭证过滤不是.
                retryable=rejection is not MemoryRejection.LOOKS_LIKE_SECRET,
            ),
        )


class MemoryWriteTool(_MemoryTool):
    _SPEC = _spec(
        "memory_write",
        "记住一件事",
        "把一条**脱离本次对话之后依然成立**的事实记下来, 供以后的会话使用. "
        "只记可验证的事实与用户明确说过的偏好; "
        "不要记推断, 猜测, 进度或决定 (那些属于计划), 更不要记任何凭证. "
        "同一个 key 再写一次会覆盖旧值.",
        {
            "scope": {"type": "string", "description": _SCOPE_DESCRIPTION},
            "key": {
                "type": "string",
                "description": "蛇形小写的短标识, 如 test_command",
            },
            "value": {"type": "string", "description": "结论本身, 一两句话"},
            "derived_from": {
                "type": "string",
                "description": "你是据什么记下这一条的, 一句话",
            },
        },
        ("scope", "key", "value"),
    )

    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        key = str(plan.normalized_input.get("key", ""))
        written = self._memory.remember(
            self._scope_of(plan),
            key,
            str(plan.normalized_input.get("value", "")),
            derived_from=str(plan.normalized_input.get("derived_from", "")),
        )
        if written.rejection is not None:
            return self._refused(plan, written.rejection)
        if written.replaced:
            # 说清覆盖了什么: 模型据此知道它刚推翻了自己以前的结论, 而不是新增了一条.
            return self._ok(
                plan,
                prompt_text.MEMORY_REPLACED.format(key=key, old=written.replaced),
            )
        return self._ok(plan, prompt_text.MEMORY_WRITTEN.format(key=key))


class MemoryForgetTool(_MemoryTool):
    _SPEC = _spec(
        "memory_forget",
        "忘掉一件事",
        "删掉一条不再成立的记忆. 发现记忆与当前事实不符时用它.",
        {
            "scope": {"type": "string", "description": _SCOPE_DESCRIPTION},
            "key": {"type": "string"},
        },
        ("scope", "key"),
    )

    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        key = str(plan.normalized_input.get("key", ""))
        if self._memory.forget(self._scope_of(plan), key):
            return self._ok(plan, prompt_text.MEMORY_FORGOTTEN.format(key=key))
        # 删一个不存在的 key 不是错误 —— 但模型该知道它记错了什么.
        return self._ok(plan, prompt_text.MEMORY_NOT_FOUND.format(key=key))
