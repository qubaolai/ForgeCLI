"""上下文组装: 把六层拼成一份 AssembledContext (ADR-0041 决策 1).

对外唯一入口. 分层的顺序本身在 `AssembledContext` 里表达 —— 这里只负责**取材**:
哪一层从哪儿读, 什么时候读.

三层的读取时机不同, 而那正是它们分层的理由:

- [2][3] 提示词只依赖包版本与 FORGE.md, builder 内部按进程缓存内置五块;
- [4] 运行事实每轮现取 —— 用户可能刚 /add-dir 加过根, 上一轮的事实不作数;
- [6] 计划, 待办与记忆同样每轮现读: 待办的价值就在于它反映**此刻**的执行状态.

不碰窗口: 那是 `WindowManager` 的事. 一个负责"这一轮的上下文长什么样", 一个负责"窗口
该不该淘汰", 合起来会让"为什么这一轮变贵了"再也说不清是哪一半干的.
"""

from __future__ import annotations

from forgecli.application.context.runtime_facts import RuntimeFacts
from forgecli.application.context.runtime_view import render_runtime_context
from forgecli.application.context.state_view import render_state_frame
from forgecli.application.planning.planning_service import ActivePlanning
from forgecli.application.prompt.project_instruction_reader import ProjectInstruction
from forgecli.application.prompt.system_prompt_builder import SystemPromptBuilder
from forgecli.domain.agent.state import AssembledContext
from forgecli.domain.context.budget import ContextBudget
from forgecli.domain.conversation.message import ChatMessage
from forgecli.domain.execution.fence import FencePolicy
from forgecli.domain.intents import SessionMode
from forgecli.domain.memory.entry import MemoryEntry

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
        window: tuple[ChatMessage, ...] = (),
        budget: ContextBudget | None = None,
        fence: FencePolicy | None = None,
    ) -> AssembledContext:
        return AssembledContext(
            # 加载内置的几乎不变化的提示词块
            policy=self._builder.build(instructions),
            runtime_context=render_runtime_context(facts, mode=mode, fence=fence),
            state_frame=render_state_frame(planning or ActivePlanning(), memory),
            initial_window=window,
            budget=budget,
        )
