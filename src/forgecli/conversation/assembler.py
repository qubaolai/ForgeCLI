"""上下文组装: 把一次调用的材料拼成一份 AssembledContext (ADR-0051 决策 2).

对外唯一入口. 请求的顺序在 `AssembledContext` 里表达 —— 这里只负责**取材**: 提示词块
(builder 内部按进程缓存内置五块), FORGE.md, 运行事实, 工具目录, 计划, 待办, 记忆.
外层每次调模型前调一次; 输入没变, 组出来的就是同样的字节, 供应商的前缀缓存照样命中.

不碰窗口: 那是 `WindowManager` 的事. 一个负责"这次调用的上下文长什么样", 一个负责"窗口
该不该淘汰", 合起来会让"为什么这一轮变贵了"再也说不清是哪一半干的.
"""

from __future__ import annotations

from forgecli.conversation.runtime_facts import RuntimeFacts
from forgecli.conversation.runtime_view import render_runtime_context
from forgecli.conversation.state_view import render_state_frame
from forgecli.core.agent.state import AssembledContext
from forgecli.core.context.budget import ContextBudget
from forgecli.core.execution.fence import FencePolicy
from forgecli.core.mode import SessionMode
from forgecli.core.tool.tool_call import ToolSchema
from forgecli.memory.entry import MemoryEntry
from forgecli.planning.service import ActivePlanning
from forgecli.prompt.project_instruction_reader import ProjectInstruction
from forgecli.prompt.system_prompt_builder import SystemPromptBuilder

__all__ = ["ContextAssembler"]


class ContextAssembler:
    """无状态. 每次从传进来的事实重新组装."""

    def __init__(self, builder: SystemPromptBuilder | None = None) -> None:
        self._builder = builder or SystemPromptBuilder()

    def assemble(
        self,
        *,
        mode: SessionMode,
        facts: RuntimeFacts,
        instructions: tuple[ProjectInstruction, ...] = (),
        planning: ActivePlanning | None = None,
        memory: tuple[MemoryEntry, ...] = (),
        tools: tuple[ToolSchema, ...] = (),
        budget: ContextBudget | None = None,
        fence: FencePolicy | None = None,
    ) -> AssembledContext:
        return AssembledContext(
            # 加载内置的几乎不变化的提示词块
            policy=self._builder.build(instructions),
            runtime_context=render_runtime_context(facts, mode=mode, fence=fence),
            state_frame=render_state_frame(planning or ActivePlanning(), memory),
            tools=tools,
            budget=budget,
        )
