"""工具目录的两份清单, 以及动作分组.

界面上有两个不同的问题, 后端因此发两份清单 (`GET /api/v1/tools`):

- `items` 是当前模式下模型可见的工具, 管理面板要的就是这个语义;
- `all` 是全部已注册工具的展示名, 与模式无关 —— 运行过程视图要把历史事件里的工具名
  翻成中文, 而那些调用可能发生在换模式之前.

前端曾经为此自带一张手抄表, 于是 `artifact_read` 在后端叫"读回已归档的输出", 在界面上
叫"读取产物", 而没有任何一层会因此报错.
"""

from __future__ import annotations

from forgecli.application.prompt.system_prompt_builder import ToolBrief, _tool_groups
from forgecli.application.prompt.template_renderer import render_block
from forgecli.application.tool_request.catalog_predicates import catalog_query_for_mode
from forgecli.application.tools.registry import ToolRegistry
from forgecli.application.tools.tool import Tool, ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.intents import SessionMode
from forgecli.domain.prompt.blocks import PromptBlockId
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.errors import PreparationError
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.tool.result import ToolResult
from forgecli.domain.tool.spec import (
    TargetDeclarationAbility,
    ToolAction,
    ToolSpec,
)
from forgecli.shared.cancellation import CancelToken


class _StubTool(Tool):
    def __init__(self, spec: ToolSpec) -> None:
        self._spec = spec

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def prepare(
        self, request: ToolInvocationRequest, context: ExecutionContext
    ) -> ToolPlan | PreparationError:
        raise NotImplementedError

    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        raise NotImplementedError


def _spec(name: str, *, capability: Capability, action: ToolAction) -> ToolSpec:
    return ToolSpec(
        name=name,
        version="1",
        title=f"{name} 的用途",
        description="",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        declared_capabilities=frozenset({capability}),
        target_declaration_ability=TargetDeclarationAbility.STATIC,
        default_timeout_seconds=1.0,
        action=action,
    )


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_all(
        (
            _StubTool(
                _spec(
                    "zz_read",
                    capability=Capability.WORKSPACE_READ,
                    action=ToolAction.READ,
                )
            ),
            _StubTool(
                _spec(
                    "aa_write",
                    capability=Capability.WORKSPACE_WRITE,
                    action=ToolAction.WRITE,
                )
            ),
        )
    )
    return registry


def test_the_display_listing_keeps_tools_the_plan_gate_hides() -> None:
    """这正是 `all` 存在的理由.

    plan 档看不见写工具, 但会话里换模式之前调过的那些仍然在事件里. 拿按模式过滤的目录
    去翻译它们, 结果是界面上一半调用只显示工具名.
    """
    registry = _registry()

    visible = registry.list(catalog_query_for_mode(SessionMode.PLAN)).names
    everything = tuple(spec.name for spec in registry.describe_all())

    assert visible == ("zz_read",)
    assert everything == ("aa_write", "zz_read")


def test_every_action_renders_a_group_of_its_own() -> None:
    """新增一个 ToolAction 而忘了给它中文组名, 必须当场炸掉.

    模板用 StrictUndefined 查标签表, 所以漏一条会在渲染时抛异常而不是静默少一组 ——
    后者的表现是那个工具从工具表里消失, 而模型只会以为它不存在.
    """
    tools = tuple(
        ToolBrief(
            name=f"t_{action.value}", title=f"{action.value} 的用途", action=action
        )
        for action in ToolAction
    )

    body = render_block(
        PromptBlockId.TOOL_CONTRACT,
        tool_groups=_tool_groups(tools),
        has_shell=True,
    )

    for action in ToolAction:
        assert f"t_{action.value}" in body
    # 三种定位各成一组: "检索顺序"那一节按这个粒度分流, 组名对不上它就落空了.
    assert "定位 · 按符号" in body
    assert "定位 · 按内容" in body
    assert "定位 · 按文件名" in body


def test_an_empty_action_renders_no_heading() -> None:
    """本轮目录里没有的动作不摆一个空标题 —— 那只会让模型去想自己是不是漏看了什么."""
    body = render_block(
        PromptBlockId.TOOL_CONTRACT,
        tool_groups=_tool_groups(
            (ToolBrief(name="fs_read", title="读取文件", action=ToolAction.READ),)
        ),
        has_shell=False,
    )

    # 按整行比: "执行"两个字在工具契约的散文里本来就有, 这里问的是有没有那个组标题.
    headings = {line.strip() for line in body.splitlines() if not line.startswith(" ")}
    assert "读取" in headings
    assert "执行" not in headings
    assert "写入" not in headings
