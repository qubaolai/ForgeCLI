"""循环的输入与一次调用的材料 (ADR-0010 §4.1, ADR-0056 决策 2 / 决策 8).

不保存 raw chain-of-thought; window 只保存工具结果 / 用户反馈 / 错误 / 安全摘要 /
通知.
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.core.context.budget import ContextBudget
from forgecli.core.message import ChatMessage
from forgecli.core.permissions import PermissionMode
from forgecli.core.tool.tool_call import ToolSchema


@dataclass(frozen=True)
class ContextFingerprints:
    """三个位置各一个 sha256 (ADR-0056 决策 12). 连续两次调用之间没有刷新点时它们
    必须相同; 变了而没有理由, 就是有人把每轮变的东西放进了前缀."""

    static: str
    session: str
    tools: str


@dataclass(frozen=True)
class AssembledContext:
    """一次模型调用的材料. 每次调模型前由外层现组, 循环拿来组请求.

    请求的形状 (ADR-0056 决策 2)::

        ModelRequest.tools           tools                 会话内字节锁定
        ModelRequest.system_prompt   静态段 + 会话段       只在刷新点重算
        ModelRequest.messages        窗口 (含通知)         只追加

    没有状态帧: 计划, 待办, 记忆, 模式的变化以 ``notices`` 进窗口, 由循环在组请求之前
    追加. 通知一旦进了窗口就不再改, 所以每次组装只带**这次新要发的**几条.

    prompt 是必填而不是可选: 缺提示词时主模型调用必须被阻止, 而"可选字段 + 运行期
    检查"意味着漏传的后果是一次静默降级 —— 模型照常回答, 只是没有身份, 没有安全边界,
    也不知道自己在什么平台上.

    预算是可选的: 缺预算只是不淘汰. 不能瞎猜一个窗口大小 —— 猜小了平白丢内容, 猜大了
    等于没有这道防线.
    """

    system_prompt: str
    fingerprints: ContextFingerprints
    # 模型这次能看到的工具. 目录为空表示本档不暴露任何工具.
    tools: tuple[ToolSchema, ...] = ()
    budget: ContextBudget | None = None
    # 这次要追加进窗口的通知 (决策 6). 全部 meta, 全部 <system-reminder> 包着.
    notices: tuple[ChatMessage, ...] = ()

    def __post_init__(self) -> None:
        if not self.system_prompt.strip():
            raise ValueError("AssembledContext.system_prompt 不能为空")
        for notice in self.notices:
            if not notice.meta:
                raise ValueError("AssembledContext.notices 只能装 meta 通知")


@dataclass(frozen=True)
class LoopInput:
    """AgentLoop 的入口输入: 身份, 模式, 开始时的窗口. 上下文每次调用现组, 不在这里."""

    turn_id: str
    session_id: str
    mode: PermissionMode
    initial_window: tuple[ChatMessage, ...] = ()
    # 上一轮结束时的累计淘汰数, 本轮接着数.
    evicted_count: int = 0

    def __post_init__(self) -> None:
        if not self.turn_id.strip():
            raise ValueError("LoopInput.turn_id 不能为空")
        if not self.session_id.strip():
            raise ValueError("LoopInput.session_id 不能为空")
