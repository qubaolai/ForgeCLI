"""循环输入域状态: LoopInput / LoopState 及其协作值对象（ADR-0010 §4.1 / §4.2 / §12）。

字段一次冻全。协作类型中 ModePolicy / ContextPackage / LoopBudgets 在本切片先给最小占位
形状（字段位冻结、内容后续充实）：ModePolicy 的 allow/ask/deny 能力边界在权限引擎切片
（ADR-0009）落地，ContextPackage 的上下文组装与 compact 在上下文切片落地。

关键约束（§4.2）：LoopState 可由实现重建或裁剪，**不能替代 events.jsonl + state.json**；
不保存 raw chain-of-thought；observations 只保存工具结果 / 用户反馈 / 错误 / 安全摘要 /
context 信息。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from forgecli.application.agent_loop.actions import LoopAction, LoopObservation
from forgecli.application.llm.gateway.messages import ChatMessage, ToolSpec
from forgecli.domain.intents import SessionMode, UserIntent


@dataclass(frozen=True)
class ModePolicy:
    """当前模式的能力边界(占位)

    本切片只冻字段位. 权限引擎切片（ADR-0009：deny→ask→allow 规则引擎、能力门、
    高危拦截）落地后，本类承载解析出的 allow/ask/deny 裁决入口与工具暴露边界。
    现仅携带 mode，供循环与裁决共享单一真相。
    """

    mode: SessionMode


@dataclass(frozen=True)
class ContextPackage:
    """本轮模型上下文（占位最小形状）。

    system_prompt + 归一化对话消息。上下文组装、压缩与记忆注入在后续切片充实；本切片
    只需承载「喂给 gateway 的一轮上下文」这一最小职责。
    """

    messages: tuple[ChatMessage, ...] = ()
    system_prompt: str | None = None


@dataclass(frozen=True)
class LoopBudgets:
    """一轮 / 一任务的硬停止预算（§12）。全部可空，None 表示该维度不设限。"""

    max_steps_per_turn: int | None = None
    max_tool_calls_per_turn: int | None = None
    max_model_calls_per_turn: int | None = None
    max_wall_clock_seconds: float | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_cost: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "max_steps_per_turn",
            "max_tool_calls_per_turn",
            "max_model_calls_per_turn",
            "max_wall_clock_seconds",
            "max_input_tokens",
            "max_output_tokens",
            "max_cost",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} 不能为负")


@dataclass(frozen=True)
class LoopState:
    """循环内存状态（§4.2）。frozen：状态推进用 dataclasses.replace 产生新值。

    不是唯一恢复源——可由 events.jsonl + state.json 重建或裁剪。不保存 raw CoT。
    """

    turn_id: str
    step_index: int = 0
    mode: SessionMode = SessionMode.ACCEPT_EDITS
    context_package: ContextPackage = field(default_factory=ContextPackage)
    observations: tuple[LoopObservation, ...] = ()
    pending_actions: tuple[LoopAction, ...] = ()
    budgets: LoopBudgets = field(default_factory=LoopBudgets)
    # 每类失败的重试计数（键为失败归类，值为已重试次数）。
    retry_state: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not self.turn_id.strip():
            raise ValueError("LoopState.turn_id 不能为空")
        if self.step_index < 0:
            raise ValueError("LoopState.step_index 不能为负")


@dataclass(frozen=True)
class LoopInput:
    """AgentLoop 的入口输入（§4.1）。

    user_intent 来自 IntentRouter；tool_catalog 只含当前模式与配置允许暴露给模型的工具；
    resume_state 由 events.jsonl + state.json 还原，不依赖第三方 checkpoint。
    """

    turn_id: str
    session_id: str
    user_intent: UserIntent
    mode: SessionMode
    mode_policy: ModePolicy
    context_package: ContextPackage = field(default_factory=ContextPackage)
    tool_catalog: tuple[ToolSpec, ...] = ()
    budgets: LoopBudgets = field(default_factory=LoopBudgets)
    resume_state: LoopState | None = None

    def __post_init__(self) -> None:
        if not self.turn_id.strip():
            raise ValueError("LoopInput.turn_id 不能为空")
        if not self.session_id.strip():
            raise ValueError("LoopInput.session_id 不能为空")
        if self.mode_policy.mode is not self.mode:
            raise ValueError("LoopInput.mode 与 mode_policy.mode 必须一致")
