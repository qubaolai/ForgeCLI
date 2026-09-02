"""循环输入域状态: LoopInput 及其协作值对象（ADR-0010 §4.1 / §4.2 / §12）。

不保存 raw chain-of-thought；observations 只保存工具结果 / 用户反馈 / 错误 / 安全摘要 /
context 信息。

原 ModePolicy 已删除: 它是废弃的 ADR-0009 留下的占位, 里面只包了一个 mode, 却让
LoopInput 多出一条"mode 与 mode_policy.mode 必须一致"的自洽校验。能力边界现在由两件
东西表达 —— 模型可见的工具集是 ToolCatalog (ADR-0004 §7 的目录谓词算出), 裁决口径是
安全模块的 mode 能力矩阵 (ADR-0013 §9)。循环本身不需要第三份副本。
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.context.budget import ContextBudget
from forgecli.domain.conversation.message import ChatMessage
from forgecli.domain.intents import SessionMode
from forgecli.domain.prompt.blocks import PromptSnapshot
from forgecli.domain.tool.catalog import ToolCatalog


@dataclass(frozen=True)
class ContextPackage:
    """本轮模型上下文 (ADR-0018 §3.2).

    归一化对话消息 + 本轮冻结的系统提示词 + 本轮的上下文预算.

    预算是**可选**的, 与必填的 prompt 不同 (ADR-0032 决策 1): 缺提示词必须让调用被
    阻止, 而缺预算只是不压缩. 两者的失败方向不一样 —— 没有提示词的模型会开始猜自己是
    谁, 而没有预算的压缩器不做事, 与接入压缩之前的行为完全一致. 更要紧的是不能瞎猜一个
    窗口大小: 猜小了平白压掉内容, 猜大了等于没有这道防线.

    prompt 是**必填**而不是可选: 缺提示词时主模型调用必须被阻止 (ADR-0018 §11), 而
    "可选字段 + 运行期检查"意味着漏传的后果是一次静默降级 —— 模型照常回答, 只是没有
    身份, 没有工具契约, 也不知道自己在什么平台上. 那正是接入提示词之前的状态, 不该
    还能被无意中退回去.

    system prompt 不伪装成 MessageRole.SYSTEM 的对话消息, 不进 transcript, 也不作为
    历史消息重放.
    """

    prompt: PromptSnapshot
    messages: tuple[ChatMessage, ...] = ()
    budget: ContextBudget | None = None


@dataclass(frozen=True)
class LoopInput:
    """AgentLoop 的入口输入（§4.1）。

    tool_catalog 只含当前模式与配置允许暴露给模型的工具。
    """

    turn_id: str
    session_id: str
    mode: SessionMode
    # 必填: 一轮没有上下文包等于没有提示词, 而那必须在类型层面就不可表达.
    context_package: ContextPackage
    tool_catalog: ToolCatalog | None = None

    def __post_init__(self) -> None:
        if not self.turn_id.strip():
            raise ValueError("LoopInput.turn_id 不能为空")
        if not self.session_id.strip():
            raise ValueError("LoopInput.session_id 不能为空")
