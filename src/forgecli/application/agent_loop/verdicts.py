"""规则给循环的处置 (ADR-0049 决策 2).

每个时机只允许其中几种, 见 `rule.py` 里各时机的返回类型. 一条规则在某个时机返回了不
允许的处置, mypy 直接报错 —— 所以这里没有一个"什么都能装"的通用类型.

停止不另起类型, 直接用 `LoopStop(reason, message)`.
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.application.agent_loop.model_invoker import ModelOutcome
from forgecli.domain.context.window import Window
from forgecli.domain.conversation.message import ChatMessage

__all__ = [
    "CloseTools",
    "Continue",
    "Deny",
    "Inject",
    "Reask",
    "Replace",
    "Rewrite",
]


@dataclass(frozen=True)
class Continue:
    """这一步没有意见. 在 on_model_error 里表示"这个错不归我管"."""


@dataclass(frozen=True)
class Rewrite:
    """换成这个窗口, 然后照常往下走.

    调模型之前返回它: 换了窗口, 后面的规则接着看 (看到的是换过的窗口).
    模型出错后返回它: 换了窗口, 回到"调模型之前"重来一遍.

    summary 为空就不发决策摘要.
    """

    window: Window
    summary: str = ""


@dataclass(frozen=True)
class Reask:
    """这次模型输出作废, 追加几条消息, 重新问一次.

    不写 assistant 消息: 那次输出里没有一条能用的工具调用, 写进去就欠下一批永远等不到
    的工具结果.
    """

    messages: tuple[ChatMessage, ...]
    summary: str = ""


@dataclass(frozen=True)
class Replace:
    """改写模型这次的输出, 比如剥掉已经不该派发的工具调用."""

    outcome: ModelOutcome


@dataclass(frozen=True)
class Deny:
    """这一个调用不执行, 用这段文字当它的工具结果. 队列里其余的照常派发."""

    notice: str
    code: str


@dataclass(frozen=True)
class Inject:
    """追加消息.

    由循环保证在整批工具结果都回填完之后才写: 插在两条工具结果中间会打断 assistant 的
    tool_calls 与配对结果之间的连续区, 供应商会拒.
    """

    messages: tuple[ChatMessage, ...]
    summary: str = ""


@dataclass(frozen=True)
class CloseTools:
    """收掉本轮的工具目录. 给排队中的调用补工具结果, 追加通知, 都由循环做."""

    notice: str
