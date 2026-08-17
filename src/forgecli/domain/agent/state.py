"""循环输入域状态: LoopInput / LoopState 及其协作值对象（ADR-0010 §4.1 / §4.2 / §12）。

关键约束（§4.2）：LoopState 可由实现重建或裁剪，**不能替代 events.jsonl + state.json**；
不保存 raw chain-of-thought；observations 只保存工具结果 / 用户反馈 / 错误 / 安全摘要 /
context 信息。

原 ModePolicy 已删除: 它是废弃的 ADR-0009 留下的占位, 里面只包了一个 mode, 却让
LoopInput 多出一条"mode 与 mode_policy.mode 必须一致"的自洽校验。能力边界现在由两件
东西表达 —— 模型可见的工具集是 ToolCatalog (ADR-0004 §7 的目录谓词算出), 裁决口径是
安全模块的 mode 能力矩阵 (ADR-0013 §9)。循环本身不需要第三份副本。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from forgecli.domain.agent.actions import LoopAction, LoopObservation
from forgecli.domain.agent.prompt import PromptSnapshot
from forgecli.domain.conversation.message import ChatMessage
from forgecli.domain.intents import SessionMode, UserIntent
from forgecli.domain.tool.catalog import ToolCatalog


@dataclass(frozen=True)
class ContextPackage:
    """本轮模型上下文 (ADR-0018 §3.2).

    归一化对话消息 + 本轮冻结的系统提示词. 上下文压缩与记忆注入还没有产出方, 因此不在
    这里预留字段.

    prompt 是**必填**而不是可选: 缺提示词时主模型调用必须被阻止 (ADR-0018 §11), 而
    "可选字段 + 运行期检查"意味着漏传的后果是一次静默降级 —— 模型照常回答, 只是没有
    身份, 没有工具契约, 也不知道自己在什么平台上. 那正是接入提示词之前的状态, 不该
    还能被无意中退回去.

    system prompt 不伪装成 MessageRole.SYSTEM 的对话消息, 不进 transcript, 也不作为
    历史消息重放.
    """

    prompt: PromptSnapshot
    messages: tuple[ChatMessage, ...] = ()


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
    # 可空而不是 default_factory: ContextPackage 现在必须带提示词, 造不出空实例了.
    context_package: ContextPackage | None = None
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
    # 必填: 一轮没有上下文包等于没有提示词, 而那必须在类型层面就不可表达.
    context_package: ContextPackage
    tool_catalog: ToolCatalog | None = None
    budgets: LoopBudgets = field(default_factory=LoopBudgets)
    resume_state: LoopState | None = None

    def __post_init__(self) -> None:
        if not self.turn_id.strip():
            raise ValueError("LoopInput.turn_id 不能为空")
        if not self.session_id.strip():
            raise ValueError("LoopInput.session_id 不能为空")
