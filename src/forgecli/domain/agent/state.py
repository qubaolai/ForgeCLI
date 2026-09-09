"""循环输入域状态: LoopInput 及其协作值对象 (ADR-0010 §4.1 / §4.2, ADR-0041 决策 1).

不保存 raw chain-of-thought; window 只保存工具结果 / 用户反馈 / 错误 / 安全摘要 /
context 信息.

原 ModePolicy 已删除: 它是废弃的 ADR-0009 留下的占位, 里面只包了一个 mode, 却让
LoopInput 多出一条"mode 与 mode_policy.mode 必须一致"的自洽校验. 能力边界现在由两件
东西表达 —— 模型可见的工具集是 ToolCatalog (ADR-0004 §7 的目录谓词算出), 裁决口径是
安全模块的 mode 能力矩阵 (ADR-0013 §9). 循环本身不需要第三份副本.
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.context.budget import ContextBudget
from forgecli.domain.conversation.message import ChatMessage, TextBlock
from forgecli.domain.conversation.turn import MessageRole
from forgecli.domain.intents import SessionMode
from forgecli.domain.prompt.blocks import PromptSnapshot
from forgecli.domain.tool.catalog import ToolCatalog


@dataclass(frozen=True)
class AssembledContext:
    """本轮发给模型的上下文, 按变更源分层.

    一次请求的六层, 顺序即层号, 每一层的变更频率不高于它前面那一层::

        [1] 工具目录        模式级      ModelRequest.tools
        [2] 内置静态策略    包版本级  ┐
        [3] 工作区指令      会话级快照├ ModelRequest.system_prompt
        [4] 运行上下文      模式级    ┘
        [5] 会话窗口        只追加    ┐ ModelRequest.messages
        [6] 当前状态帧      每轮重建  ┘

    [1] 到 [4] 同属缓存前缀, 一起命中或一起失效; [5] 只在尾部增长; [6] 每轮重建, 但它是
    整条请求的最后一个内容块, 后面没有任何东西会被它作废. 用户输入不是独立一层
    —— 它是 [5] 里的一条普通消息, 因为多步回合里最后一条是 tool result 而不是用户那句话.

    [4] 排在 [3] 之后而不是甩到尾部, 理由在 `application/context/runtime_view`: 它的变化
    是 [1] 变化的子集, 进前缀的额外成本恰好为零. [6] 恰好相反 —— 计划与待办每轮都可能变,
    与 [1] 无关, 所以它只能待在末尾.

    prompt 是必填而不是可选: 缺提示词时主模型调用必须被阻止, 而
    "可选字段 + 运行期检查"意味着漏传的后果是一次静默降级 —— 模型照常回答, 只是没有
    身份, 没有安全边界, 也不知道自己在什么平台上.

    预算是可选的, 与必填的 policy 不同: 缺提示词必须让调用被阻止, 而缺预算只是不
    淘汰. 两者的失败方向不一样, 而且不能瞎猜一个窗口大小 —— 猜小了平白丢内容, 猜大了
    等于没有这道防线.
    """

    policy: PromptSnapshot
    runtime_context: str = ""
    state_frame: str = ""
    initial_window: tuple[ChatMessage, ...] = ()
    budget: ContextBudget | None = None

    @property
    def system_prompt(self) -> str:
        """[2][3] 与 [4] 拼成一条 system 消息.

        运行上下文接在静态策略之后: 前面那段几十轮不变, 后面这段换档才变, 而缓存只认从头
        开始的前缀.
        """
        if not self.runtime_context:
            return self.policy.text
        return f"{self.policy.text}\n\n{self.runtime_context}"

    def to_request_messages(
        self, window: tuple[ChatMessage, ...]
    ) -> tuple[ChatMessage, ...]:
        """[5] 会话窗口 + [6] 状态帧.

        window 由调用方传进来而不是取 `initial_window`: 它在回合内随每次工具往返增长,
        而这个值对象是回合开始时冻结的. 传进来也让"状态帧永远在最后"这条不变量只在这一个
        函数里成立 —— 循环那边拼一次, 上下文管理那边再拼一次, 迟早有一处漏掉.

        状态帧不进窗口: 它每轮重建, 进了窗口就意味着下一轮的历史里躺着一份过时的
        副本, 而摘掉它又是一次中段改写.
        """
        if not self.state_frame:
            return window
        frame = ChatMessage(
            role=MessageRole.USER, content=(TextBlock(text=self.state_frame),)
        )
        return (*window, frame)


@dataclass(frozen=True)
class LoopInput:
    """AgentLoop 的入口输入.

    tool_catalog 只含当前模式与配置允许暴露给模型的工具.
    """

    turn_id: str
    session_id: str
    mode: SessionMode
    # 必填: 一轮没有上下文等于没有提示词, 而那必须在类型层面就不可表达.
    context: AssembledContext
    tool_catalog: ToolCatalog | None = None

    def __post_init__(self) -> None:
        if not self.turn_id.strip():
            raise ValueError("LoopInput.turn_id 不能为空")
        if not self.session_id.strip():
            raise ValueError("LoopInput.session_id 不能为空")
