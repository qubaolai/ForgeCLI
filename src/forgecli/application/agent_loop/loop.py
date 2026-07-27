"""AgentLoop 端口：受控 ReAct 内核（ADR-0010 §3 / §5）。

AgentLoop 是 ReAct 内核，维护循环状态并产出结构化 LoopDecision / LoopAction / LoopStop。
它只产出意图，不执行任何操作——这条边界,保证编排内核
将来可替换（ADR-0010 §3）：换掉它时，事件流、权限审批、工具运行时都不受影响。

驱动协议（stepwise，对齐 §5 时序图）：AgentTurnService 用 start() 起一轮，拿到首个
LoopStepResult；若是带动作的 LoopDecision，则由 service 裁决 + 执行动作，再把
LoopObservation 经 observe() 回填，拿到下一步；直到得到 LoopStop 结束本轮。
BuiltinAgentLoop 的具体步进语义在 2026-07-24 落地，今日只冻端口形状。

约束（§14 验收）：AgentLoop 不 import CLI / filesystem / shell / git / MCP SDK 或具体
LLM SDK。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from forgecli.application.agent_loop.actions import LoopObservation, LoopStepResult
from forgecli.application.agent_loop.state import LoopInput


class AgentLoop(ABC):
    """受控 ReAct 内核端口。只产出意图，不执行副作用。"""

    @abstractmethod
    def start(self, loop_input: LoopInput) -> LoopStepResult:
        """起一轮循环，返回首个步进结果（带动作的决策，或直接停止）。"""

    @abstractmethod
    def observe(self, observation: LoopObservation) -> LoopStepResult:
        """把动作执行后的观察回填循环，返回下一步进结果。"""
