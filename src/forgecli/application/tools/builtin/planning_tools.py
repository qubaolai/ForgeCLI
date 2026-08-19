"""计划与待办的五个专用工具 (ADR-0022 决策 5).

`plan.read` / `plan.write` / `todo.read` / `todo.write` / `todo.set_status`.

**这五个工具不经过审批, 而理由不是"计划这件事不重要".**

- 表层原因: 它们只声明 `PLAN_ONLY`, 而 `_PLAN` 集合是四档模式预算的公共子集,
  `PolicyEngine` 另有 `PLAN_ONLY_FAST_PATH`. "不经 ASK"不是为它们新开的特例.
- 真正的原因: **它们的 input_schema 里没有路径字段.** 写入位置由 Forge 从项目 id 与
  plan_id 算出来, 模型无法指定写到哪里. 没有可由模型影响的目标, 就没有可裁决的内容.
  对照 `fs.write_patch` —— 它的路径来自模型, 所以必须逐次裁决. **是"模型能不能选目标"
  决定要不要审批, 不是"这件事重不重要".**

顺带回答"为什么不直接用 fs.* 读写计划文件": 计划目录在任何工作区根之外, 那条路会落
EXTERNAL_READ / EXTERNAL_WRITE 从而逐次 ASK; 更要紧的是它把 `~/.forge` 下的**任意位置**
暴露给一个由模型填写的路径参数. 专用工具是更窄的通道, 不是更宽的.

边界: 不经审批 != 不记审计. 五个工具照常经 ToolRequestCoordinator, 照常写 tool_requested
与 tool_completed, 照常受 ToolRuntime 的 spec 上界与授权信封校验. 少的只有 ASK 那一步.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from forgecli.application.planning import PlanningService, StatusUpdate
from forgecli.application.tools.builtin.base import validate_arguments
from forgecli.application.tools.tool import Tool, ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.planning import (
    MAX_PLAN_STEPS,
    MAX_TODO_ITEMS,
    PlanStep,
    TodoStatus,
)
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
    TurnDisposition,
)
from forgecli.domain.tool.spec import TargetDeclarationAbility, ToolSpec
from forgecli.shared.cancellation import CancelToken

__all__ = [
    "PlanReadTool",
    "PlanWriteTool",
    "TodoReadTool",
    "TodoSetStatusTool",
    "TodoWriteTool",
]

_TODO_STATUSES = tuple(status.value for status in TodoStatus)


def _spec(
    name: str,
    title: str,
    description: str,
    properties: Mapping[str, object],
    required: Sequence[str] = (),
) -> ToolSpec:
    """五个工具共用的声明形状.

    `additionalProperties: False` 不是洁癖: 它连同"没有路径字段"一起, 构成了这批工具
    目标集合在**机制上**封闭的证明. 允许额外字段等于允许将来某个实现悄悄加一个路径参数.
    """
    return ToolSpec(
        name=name,
        version="1",
        title=title,
        description=description,
        input_schema={
            "type": "object",
            "properties": dict(properties),
            "required": list(required),
            "additionalProperties": False,
        },
        output_schema={"type": "object", "properties": {"body": {"type": "string"}}},
        declared_capabilities=frozenset({Capability.PLAN_ONLY}),
        target_declaration_ability=TargetDeclarationAbility.STATIC,
        default_timeout_seconds=5.0,
    )


class _PlanningTool(Tool):
    """共用 prepare: 目标集合恒为空, 因为模型不指定任何目标."""

    _SPEC: ToolSpec

    def __init__(self, planning: PlanningService) -> None:
        self._planning = planning

    @property
    def spec(self) -> ToolSpec:
        return self._SPEC

    def prepare(
        self, request: ToolInvocationRequest, context: ExecutionContext
    ) -> ToolPlan | PreparationError:
        invalid = validate_arguments(self._SPEC, request.arguments)
        if invalid is not None:
            return invalid
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=self._SPEC.name,
            spec_hash=self._SPEC.spec_hash,
            normalized_input=MappingProxyType(dict(request.arguments)),
            capabilities=frozenset({Capability.PLAN_ONLY}),
            # 空 effects 是事实, 不是省略: 这些工具碰不到工作区里的任何东西, 写入位置
            # 也不由模型决定.
            effects=PlanEffects(),
            target_resolution=TargetResolution.STATIC,
            workspace_scope=WorkspaceScope.IN_WORKSPACE,
            execution_context=context.to_ref(),
            declaration_confidence=DeclarationConfidence.DECLARED,
        )

    def _ok(
        self,
        plan: ToolPlan,
        body: str,
        *,
        disposition: TurnDisposition = TurnDisposition.CONTINUE,
    ) -> ToolResult:
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=self._SPEC.name,
            status=ToolResultStatus.OK,
            content_parts=(ContentPart(text=body),),
            turn_disposition=disposition,
        )


# ---- 计划 ----


class PlanReadTool(_PlanningTool):
    _SPEC = _spec(
        "plan.read",
        "读取计划",
        "读当前生效计划的正文. 传 plan_id 可以读指定的一份.",
        {"plan_id": {"type": "string"}},
    )

    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        plan_id = str(plan.normalized_input.get("plan_id", ""))
        body = self._planning.read_plan(plan_id)
        if body is None:
            # 说清是"没有"而不是"读失败": 前者该去提一份计划, 后者该找人.
            return self._ok(
                plan,
                "当前没有生效的计划. 需要先对齐大方向时用 plan.write 提一份.",
            )
        return self._ok(plan, body)


class PlanWriteTool(_PlanningTool):
    _SPEC = _spec(
        "plan.write",
        "提交计划",
        (
            "提交一份计划供用户裁决. 传 plan_id 表示为已有计划提交新的一版. "
            "只给结构化字段, 格式由 Forge 按固定模板渲染."
        ),
        {
            "title": {"type": "string"},
            "goal": {"type": "string"},
            "context": {"type": "string"},
            "approach": {"type": "string"},
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_PLAN_STEPS,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "detail": {"type": "string"},
                    },
                    "required": ["title"],
                    "additionalProperties": False,
                },
            },
            "risks": {"type": "array", "items": {"type": "string"}},
            "acceptance": {"type": "array", "items": {"type": "string"}},
            "plan_id": {"type": "string"},
        },
        # risks 也在 required 里而允许空数组, 是为了强制模型**表态**: 空数组表示"想过了,
        # 没有"; 不给这个键则说明它根本没考虑. 两者不该无法区分.
        required=(
            "title",
            "goal",
            "context",
            "approach",
            "steps",
            "risks",
            "acceptance",
        ),
    )

    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        arguments = plan.normalized_input
        steps = [
            PlanStep(
                title=str(item.get("title", "")),
                detail=str(item.get("detail", "")),
            )
            for item in _mappings(arguments.get("steps"))
        ]
        document = self._planning.write_plan(
            title=str(arguments.get("title", "")),
            goal=str(arguments.get("goal", "")),
            context=str(arguments.get("context", "")),
            approach=str(arguments.get("approach", "")),
            steps=steps,
            risks=_strings(arguments.get("risks")),
            acceptance=_strings(arguments.get("acceptance")),
            plan_id=str(arguments.get("plan_id", "")),
        )
        body = self._planning.read_plan(document.plan_id) or ""
        # 声明"本次输出需要人裁决". 循环据此在回合边界停下 (ADR-0023 决策 1) —— 它不认识
        # plan.write 这个名字, 只看这个字段.
        return self._ok(plan, body, disposition=TurnDisposition.AWAIT_USER_DECISION)


# ---- 待办 ----


class TodoReadTool(_PlanningTool):
    _SPEC = _spec("todo.read", "读取待办", "读当前待办清单.", {})

    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        todo = self._planning.read_todo()
        if todo is None:
            return self._ok(plan, "当前没有待办清单.")
        return self._ok(plan, todo.render())


class TodoWriteTool(_PlanningTool):
    _SPEC = _spec(
        "todo.write",
        "重写待办",
        (
            "用一份新清单整表替换当前待办. 用于步骤拆错, 顺序不对或需要增删时纠正内容; "
            "全部状态会重置为 pending. 只改状态请用 todo.set_status."
        ),
        {
            "items": {
                "type": "array",
                "maxItems": MAX_TODO_ITEMS,
                "items": {"type": "string"},
            }
        },
        required=("items",),
    )

    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        titles = _strings(plan.normalized_input.get("items"))
        todo = self._planning.write_todo(titles)
        return self._ok(plan, todo.render())


class TodoSetStatusTool(_PlanningTool):
    _SPEC = _spec(
        "todo.set_status",
        "更新待办状态",
        (
            "改一项或多项待办的状态. index 从 0 起, 与 todo.read 打印的序号一致. "
            "同一时刻最多一条 in_progress."
        ),
        {
            "updates": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "minimum": 0},
                        "status": {"type": "string", "enum": list(_TODO_STATUSES)},
                    },
                    "required": ["index", "status"],
                    "additionalProperties": False,
                },
            }
        },
        required=("updates",),
    )

    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        updates = [
            StatusUpdate(
                index=_index_of(item.get("index")),
                status=TodoStatus(str(item.get("status", ""))),
            )
            for item in _mappings(plan.normalized_input.get("updates"))
        ]
        try:
            todo = self._planning.update_status(updates)
        except ValueError as exc:
            # 序号越界或撞上"最多一条 in_progress". 这是模型能自己改正的普通失败, 不该
            # 走安全裁决, 也不该变成一次工具异常.
            return ToolResult(
                invocation_id=plan.plan_id,
                tool_name=self._SPEC.name,
                status=ToolResultStatus.TOOL_ERROR,
                content_parts=(ContentPart(text=str(exc)),),
                error=ToolError(
                    code=PreparationErrorCode.INVALID_INPUT.value, message=str(exc)
                ),
            )
        if todo is None:
            return self._ok(plan, "当前没有待办清单, 先用 todo.write 建一份.")
        return self._ok(plan, todo.render())


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    return tuple(str(item) for item in value)


def _index_of(value: object) -> int:
    """schema 保证它是整数; 这里只是把类型收窄, 非整数一律给 -1 让不变量去报错."""
    return value if isinstance(value, int) else -1


def _mappings(value: object) -> tuple[Mapping[str, object], ...]:
    """schema 已经校验过形状, 这里只是把类型收窄回来."""
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))
