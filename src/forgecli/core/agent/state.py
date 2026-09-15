"""循环的输入与一次调用的材料 (ADR-0010 §4.1, ADR-0051 决策 2).

不保存 raw chain-of-thought; window 只保存工具结果 / 用户反馈 / 错误 / 安全摘要 /
context 信息.
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.core.context.budget import ContextBudget
from forgecli.core.message import ChatMessage, TextBlock
from forgecli.core.mode import SessionMode
from forgecli.core.prompt_blocks import PromptSnapshot
from forgecli.core.tool.tool_call import ToolSchema
from forgecli.core.turn import MessageRole


@dataclass(frozen=True)
class AssembledContext:
    """一次模型调用的材料. 每次调模型前由外层现组, 循环拿来组请求.

    请求的顺序是**稳的在前, 变的在后**, 因为供应商按字节前缀命中缓存::

        ModelRequest.system_prompt   policy (策略块 + FORGE.md) + runtime_context
        ModelRequest.tools           tools
        ModelRequest.messages        窗口 + state_frame

    一轮里输入没变, 前面那段每次组出来都是同样的字节, 缓存照样命中; 模型真改了
    FORGE.md 或记忆, 那一次不命中, 应该的. 状态帧 (计划, 待办, 记忆) 是最后一块, 后面
    没有东西会被它作废, 所以它每次都能是新的.

    prompt 是必填而不是可选: 缺提示词时主模型调用必须被阻止, 而"可选字段 + 运行期
    检查"意味着漏传的后果是一次静默降级 —— 模型照常回答, 只是没有身份, 没有安全边界,
    也不知道自己在什么平台上.

    预算是可选的: 缺预算只是不淘汰. 不能瞎猜一个窗口大小 —— 猜小了平白丢内容, 猜大了
    等于没有这道防线.
    """

    policy: PromptSnapshot
    runtime_context: str = ""
    state_frame: str = ""
    # 模型这次能看到的工具. 目录为空表示本档不暴露任何工具.
    tools: tuple[ToolSchema, ...] = ()
    budget: ContextBudget | None = None

    @property
    def system_prompt(self) -> str:
        """策略块 + FORGE.md 与运行上下文拼成一条 system 消息.

        运行上下文接在静态策略之后: 前面那段几十轮不变, 后面这段换档才变, 而缓存只认从头
        开始的前缀.
        """
        if not self.runtime_context:
            return self.policy.text
        return f"{self.policy.text}\n\n{self.runtime_context}"

    def to_request_messages(
        self, window: tuple[ChatMessage, ...]
    ) -> tuple[ChatMessage, ...]:
        """窗口 + 状态帧.

        "状态帧永远在最后"这条只在这一个函数里成立 —— 循环那边拼一次, 上下文管理那边
        再拼一次, 迟早有一处漏掉.

        状态帧不进窗口: 它每次重建, 进了窗口就意味着历史里躺着一份过时的副本, 而摘掉它
        又是一次中段改写.
        """
        if not self.state_frame:
            return window
        frame = ChatMessage(
            role=MessageRole.USER, content=(TextBlock(text=self.state_frame),)
        )
        return (*window, frame)


@dataclass(frozen=True)
class LoopInput:
    """AgentLoop 的入口输入: 身份, 模式, 开始时的窗口. 上下文每次调用现组, 不在这里."""

    turn_id: str
    session_id: str
    mode: SessionMode
    initial_window: tuple[ChatMessage, ...] = ()

    def __post_init__(self) -> None:
        if not self.turn_id.strip():
            raise ValueError("LoopInput.turn_id 不能为空")
        if not self.session_id.strip():
            raise ValueError("LoopInput.session_id 不能为空")
