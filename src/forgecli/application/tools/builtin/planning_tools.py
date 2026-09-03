"""计划与待办的四个专用工具 (ADR-0022 决策 5).

`plan_read` / `plan_write` / `todo_write` / `todo_set_status`.

没有 `todo_read`: 待办全文每轮由 `todo_state` 块进提示词, 两个写工具的返回值也是渲染
后的同一份清单. 一个读回模型手上已有内容的工具, 占的是工具表的位置与一次选择的机会.
计划则相反 —— `plan_state` 块刻意只放元数据, 正文只能靠 `plan_read` 取.

**这四个工具不经过审批, 而理由不是"计划这件事不重要".**

- 表层原因: 它们只声明 `PLAN_ONLY`, 而 `_PLAN` 集合是四档模式预算的公共子集,
  `PolicyEngine` 另有 `PLAN_ONLY_FAST_PATH`. "不经 ASK"不是为它们新开的特例.
- 真正的原因: **它们的 input_schema 里没有路径字段.** 写入位置由 Forge 从项目 id 与
  plan_id 算出来, 模型无法指定写到哪里. 没有可由模型影响的目标, 就没有可裁决的内容.
  对照 `fs_apply_patch` —— 它的路径来自模型, 所以必须逐次裁决. **是"模型能不能选目标"
  决定要不要审批, 不是"这件事重不重要".**

顺带回答"为什么不直接用 fs_* 读写计划文件": 计划目录在任何工作区根之外, 那条路会落
EXTERNAL_READ / EXTERNAL_WRITE 从而逐次 ASK; 更要紧的是它把 `~/.forge` 下的**任意位置**
暴露给一个由模型填写的路径参数. 专用工具是更窄的通道, 不是更宽的.

边界: 不经审批 != 不记审计. 四个工具照常经 ToolRequestCoordinator, 照常写 tool_requested
与 tool_completed, 照常受 ToolRuntime 的 spec 上界与授权信封校验. 少的只有 ASK 那一步.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from forgecli.application.planning.planning_service import (
    PlanningService,
    StatusUpdate,
    is_safe_plan_id,
)
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
from forgecli.domain.tool.spec import (
    TargetDeclarationAbility,
    ToolSpec,
)
from forgecli.shared.cancellation import CancelToken

__all__ = [
    "PlanReadTool",
    "PlanWriteTool",
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
    """四个工具共用的声明形状.

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
        semantic_error = self._validate_semantics(request.arguments)
        if semantic_error is not None:
            return semantic_error
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

    def _validate_semantics(
        self, arguments: Mapping[str, object]
    ) -> PreparationError | None:
        return None

    def _ok(
        self,
        plan: ToolPlan,
        body: str,
        *,
        summary: str = "",
        disposition: TurnDisposition = TurnDisposition.CONTINUE,
    ) -> ToolResult:
        """摘要缺省取工具自己的 title.

        计划与待办的结果本来就短 (一份清单, 一句确认), 摘要与正文差不多长. 逐个编一句
        比"重写待办"更有信息量的话反而是噪音 —— 需要更具体的那几处显式传 summary.
        """
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=self._SPEC.name,
            status=ToolResultStatus.OK,
            summary=summary or self._SPEC.title,
            content_parts=(ContentPart(text=body),),
            turn_disposition=disposition,
        )


# ---- 计划 ----


class PlanReadTool(_PlanningTool):
    _SPEC = _spec(
        "plan_read",
        "读取计划",
        "读当前生效计划的正文. 传 plan_id 可以读指定的一份.",
        {"plan_id": {"type": "string"}},
    )

    def _validate_semantics(
        self, arguments: Mapping[str, object]
    ) -> PreparationError | None:
        plan_id = str(arguments.get("plan_id", ""))
        if plan_id and not is_safe_plan_id(plan_id):
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message="plan_id 只能包含字词字符和连字符，不能包含路径",
                field_path="plan_id",
            )
        return None

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
                "当前没有生效的计划. 需要先对齐大方向时用 plan_write 提一份.",
            )
        return self._ok(plan, body)


class PlanWriteTool(_PlanningTool):
    _SPEC = _spec(
        "plan_write",
        "提交计划",
        (
            "提交一份计划供用户裁决. 传 plan_id 表示为已有计划提交新的一版. "
            "只给结构化字段, 格式由 Forge 按固定模板渲染.\n"
            "待办不需要计划: 直接用 todo_write 从需求建清单是常态. "
            "只有需要人先对齐大方向时才用它, 而提交会停下来等用户裁决 —— "
            "拿到裁决之前不要按已批准处理."
        ),
        {
            "name": {
                "type": "string",
                "description": (
                    "这份计划的短名, 用作目录名与索引显示, 请按任务本身命名, "
                    "例如 web-shutdown-fix 或 修复Web退出卡住. "
                    "只在新建时使用; 为已有计划提交新版本时忽略."
                ),
                "maxLength": 60,
            },
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
            "name",
            "title",
            "goal",
            "context",
            "approach",
            "steps",
            "risks",
            "acceptance",
        ),
    )

    def _validate_semantics(
        self, arguments: Mapping[str, object]
    ) -> PreparationError | None:
        plan_id = str(arguments.get("plan_id", ""))
        if not plan_id:
            return None
        if not is_safe_plan_id(plan_id):
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message="plan_id 只能包含字词字符和连字符，不能包含路径",
                field_path="plan_id",
            )
        if self._planning.read_plan(plan_id) is None:
            return PreparationError(
                code=PreparationErrorCode.TARGET_NOT_FOUND,
                message=f"不能修订不存在的计划: {plan_id}",
                field_path="plan_id",
            )
        return None

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
            name=str(arguments.get("name", "")),
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
        # plan_write 这个名字, 只看这个字段.
        return self._ok(plan, body, disposition=TurnDisposition.AWAIT_USER_DECISION)


# ---- 待办 ----


class TodoWriteTool(_PlanningTool):
    _SPEC = _spec(
        "todo_write",
        "重写待办",
        (
            "用一份新清单整表替换当前待办. 用于步骤拆错, 顺序不对或需要增删时纠正内容; "
            "全部状态会重置为 pending. 只改状态请用 todo_set_status.\n"
            "以下任一条成立就先建清单再动手: 要改 3 个以上文件或跨多个模块; "
            "用户一句话里有多个可以分别交付的诉求; 要跨阶段推进 (改代码 -> 跑测试 -> "
            "更新文档); 已经做了几轮工具调用还没收口, 自己也说不清还剩几步.\n"
            "单文件的小改, 纯问答, 只读的探查不要建清单: 清单每轮都进上下文, "
            "给一件两三步就完的事建清单只是噪音.\n"
            "建了就要用, 清单与实际不符时用它重写整表."
        ),
        {
            "name": {
                "type": "string",
                "description": (
                    "这份清单的短名, 请按任务本身命名. 沿用当前清单的名字表示继续修正 "
                    "同一份清单; 换一个名字表示这是另一件事的清单, 旧的进归档."
                ),
                "maxLength": 60,
            },
            "items": {
                "type": "array",
                "maxItems": MAX_TODO_ITEMS,
                "items": {"type": "string"},
            },
        },
        required=("name", "items"),
    )

    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        titles = _strings(plan.normalized_input.get("items"))
        todo = self._planning.write_todo(
            titles, name=str(plan.normalized_input.get("name", ""))
        )
        return self._ok(plan, todo.render())


class TodoSetStatusTool(_PlanningTool):
    _SPEC = _spec(
        "todo_set_status",
        "更新待办状态",
        (
            "改一项或多项待办的状态. index 从 0 起, 与待办清单打印的序号一致. "
            "同一时刻最多一条 in_progress.\n"
            "开始一项之前把它设成 in_progress, 做完立刻设 done, "
            "不再需要的设 dropped —— 拖着不更新, 清单就不再反映此刻的执行状态."
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
                summary=f"更新待办状态失败: {exc}",
                error=ToolError(
                    code=PreparationErrorCode.INVALID_INPUT.value, message=str(exc)
                ),
            )
        if todo is None:
            return self._ok(plan, "当前没有待办清单, 先用 todo_write 建一份.")
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
