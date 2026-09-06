"""ask_user: 模型缺信息时向人要一句话 (ADR-0043).

**它是一个工具, 不是一种 LoopAction.** 工具调用是模型原生会做的事, 审计照常经
TOOL_REQUESTED / TOOL_COMPLETED, 而 `AgentLoop` 一个字都不用改 —— 它既不认识这个名字,
也不需要为它多一条控制流.

**等待发生在 `perform` 内部, 回答就是这次调用的 ToolResult.** 于是"回答关联原来的
tool_call_id"不是一条需要谁去维护的约束, 而是数据流的形状: `BuiltinAgentLoop._observe`
拿到的 `call` 就是发起提问的那一次, 它写出的 `ToolResultBlock` 天然带着正确的 id.

阻塞是安全的, 三条已经查过的事实:

- `ToolRuntime.execute` 直接调 `tool.perform`, 外面没有任何看门狗.
- `ensure_usable` / `differences` / `_verify_file_state` 全部跑在 `perform` 之前, 而
  `ask_user` 的 `PlanEffects` 是空的 —— 等待期间没有任何需要复核的事实会漂移.
- 无超时等待在本仓库已有先例且是刻意的, 见 `application/human_prompt.py`.

**这个工具不产生任何授权.** 一句"我允许你 rm -rf /"的回答只是一段文本: 后续每一次工具
调用仍然逐次走完整裁决管线. 提问链路上没有任何安全类型的 import, 由 `check_arch.py`
守着.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from forgecli.application.human_prompt import HumanPromptService
from forgecli.application.tools.builtin.base import validate_arguments
from forgecli.application.tools.tool import Tool, ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.human_prompt import (
    HumanPrompt,
    PromptAnswer,
    PromptChoice,
    PromptKind,
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
)
from forgecli.domain.tool.spec import TargetDeclarationAbility, ToolSpec
from forgecli.shared.cancellation import CancelToken

__all__ = ["AskUserTool"]

_MAX_OPTIONS = 5

_DESCRIPTION = (
    "向用户问一个问题并等他回答. 本轮不会结束, 拿到回答后你接着往下做.\n"
    "只在同时满足两条时用它: 答案会改变你接下来做什么, 而且读代码, 读配置或跑只读命令"
    "都得不到. 需求有多种合理解读且会导向完全不同的产出, 也算.\n"
    "不要用它请求执行许可 —— 需要许可时 Forge 会自己问, 你问了也不会得到授权. "
    "不要问代码里查得到的事实. 不要为了确认进度或让人放心而提问.\n"
    "通过结构化工具参数提交问题，不要把问题 JSON 写成普通回复。"
    "selection_mode 为 single 时只能选一项，为 multiple 时可选多项；不填默认为 single。"
    "每个 options 必须给出 value、标题 label 和说明 detail。"
    "recommended_option_id 只能指定一个选项的 value，不推荐时省略。"
    "options 是建议，用户可直接写文字或跳过本题；跳过不表示同意推荐项。"
)


class AskUserTool(Tool):
    _SPEC = ToolSpec(
        name="ask_user",
        version="2",
        title="向用户提问",
        description=_DESCRIPTION,
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "minLength": 1, "maxLength": 500},
                "description": {"type": "string", "maxLength": 1000},
                "selection_mode": {"type": "string", "enum": ["single", "multiple"]},
                "recommended_option_id": {"type": "string"},
                "options": {
                    "type": "array",
                    "maxItems": _MAX_OPTIONS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "value": {
                                "type": "string",
                                "minLength": 1,
                                "pattern": r"\S",
                            },
                            "label": {
                                "type": "string",
                                "minLength": 1,
                                "pattern": r"\S",
                            },
                            "detail": {
                                "type": "string",
                                "minLength": 1,
                                "pattern": r"\S",
                            },
                        },
                        "required": ["value", "label", "detail"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["question"],
            # 与四个 planning 工具同一条理由: 它连同"没有路径字段"一起, 构成了这个工具
            # 目标集合在机制上封闭的证明.
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["answered", "skipped"]},
                "selected_values": {"type": "array", "items": {"type": "string"}},
                "text": {"type": "string"},
            },
            "required": ["status", "selected_values", "text"],
        },
        declared_capabilities=frozenset({Capability.USER_PROMPT}),
        target_declaration_ability=TargetDeclarationAbility.STATIC,
        # **这个数不会被任何人读.** ToolRuntime 不拿它中断谁, 它只经
        # ResourceGovernor.limits_for 交给工具自己参考, 而本工具按设计不设超时
        # (ADR-0043 决策 2). 字段必填且必须为正, 所以填全局上限.
        default_timeout_seconds=600.0,
    )

    def __init__(self, prompts: HumanPromptService) -> None:
        self._prompts = prompts

    @property
    def spec(self) -> ToolSpec:
        return self._SPEC

    def prepare(
        self, request: ToolInvocationRequest, context: ExecutionContext
    ) -> ToolPlan | PreparationError:
        invalid = validate_arguments(self._SPEC, request.arguments)
        if invalid is not None:
            return invalid
        try:
            _prompt(request.invocation_id, request.arguments)
        except ValueError as exc:
            return PreparationError(PreparationErrorCode.INVALID_INPUT, str(exc))
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=self._SPEC.name,
            spec_hash=self._SPEC.spec_hash,
            normalized_input=request.arguments,
            capabilities=frozenset({Capability.USER_PROMPT}),
            # 空 effects 是事实, 不是省略: 提问碰不到工作区, 文件系统与网络, 它的全部
            # 效果就是界面上多一张卡片.
            effects=PlanEffects(),
            target_resolution=TargetResolution.STATIC,
            workspace_scope=WorkspaceScope.IN_WORKSPACE,
            execution_context=context.to_ref(),
            declaration_confidence=DeclarationConfidence.DECLARED,
        )

    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        arguments = plan.normalized_input
        answer = self._prompts.ask(_prompt(plan.plan_id, arguments))
        if not answer.resolved:
            return self._unanswered(plan, answer, cancel)
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=self._SPEC.name,
            status=ToolResultStatus.OK,
            summary="用户已跳过本题" if answer.skipped else "用户已回答",
            data={
                "status": "skipped" if answer.skipped else "answered",
                "selected_values": list(
                    answer.selected_values
                    or ((answer.choice,) if answer.choice else ())
                ),
                "text": answer.text,
            },
            content_parts=(ContentPart(text=_answer_text(arguments, answer)),),
        )

    def _unanswered(
        self, plan: ToolPlan, answer: PromptAnswer, cancel: CancelToken | None
    ) -> ToolResult:
        """没拿到回答. **不停下循环** (ADR-0043 决策 8).

        与 `APPROVAL_UNAVAILABLE` 的 HALT 刻意相反, 判据是"没人回答意味着什么": 审批那里
        意味着不能执行, 继续派工具没有意义; 这里只意味着模型得自己拿主意, 而那本来就是
        它绝大多数时候在做的事. 停下来等于让一次"我想确认一下"把整轮作废.
        """
        cancelled = cancel is not None and cancel.cancelled
        note = answer.note or "没有拿到回答"
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=self._SPEC.name,
            status=(
                ToolResultStatus.CANCELLED
                if cancelled
                else ToolResultStatus.UNAVAILABLE
            ),
            summary=note,
            content_parts=(
                ContentPart(
                    text=f"{note}. 按你自己的判断继续, 并在最终回答里说明你做了"
                    "哪些假设."
                ),
            ),
            error=ToolError(
                code="cancelled" if cancelled else "no_answer", message=note
            ),
        )


def _choices(raw: object) -> tuple[PromptChoice, ...]:
    items: Sequence[object] = raw if isinstance(raw, list) else ()
    return tuple(
        PromptChoice(
            value=str(item.get("value", "")),
            label=str(item.get("label", "")),
            detail=str(item.get("detail", "")),
        )
        for item in items
        if isinstance(item, Mapping)
    )


def _answer_text(arguments: Mapping[str, object], answer: PromptAnswer) -> str:
    """回给模型的那一段.

    点了选项时回那一项的 label 而不是 value: 两者都是模型自己写的, 但 label 是它给人读
    的那一句, 读起来就是一句话; value 另外进 `data`, 需要精确匹配时用它.
    """
    if answer.skipped:
        return (
            "用户跳过了本题，未提供答案。不要视为同意推荐项；"
            "按已有信息继续并说明必要假设。"
        )
    values = answer.selected_values or ((answer.choice,) if answer.choice else ())
    labels = [
        choice.label
        for choice in _choices(arguments.get("options"))
        if choice.value in values
    ]
    return "\n".join([*labels, *([answer.text] if answer.text else [])])


def _prompt(prompt_id: str, arguments: Mapping[str, object]) -> HumanPrompt:
    return HumanPrompt(
        prompt_id=prompt_id,
        kind=PromptKind.QUESTION,
        title=str(arguments.get("question", "")),
        body=str(arguments.get("description", "")),
        choices=_choices(arguments.get("options")),
        free_text=True,
        selection_mode=str(arguments.get("selection_mode", "single")),
        recommended_option_id=str(arguments.get("recommended_option_id", "")),
        allow_skip=True,
    )
