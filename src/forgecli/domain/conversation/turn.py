"""一轮对话的词汇: 消息角色, turn 终态, 以及助手结果。

三者放在一起是因为它们互相定义: AssistantResponse 就是"一轮 turn 的文本 + 终态",
拆开只会让 TurnStatus 与它唯一的载体分居两处。

SessionService 写事件用 MessageRole/TurnStatus，AgentTurnService 编排也用 TurnStatus，
但 SessionService 不必依赖 application/agent_turn（避免 session ↔ agent_turn 循环）。
为将来 sub agent 预留 MessageRole.SUB_AGENT（本次不启用）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "AssistantResponse",
    "MessageRole",
    "TurnIdentity",
    "TurnPause",
    "TurnStatus",
]


class MessageRole(Enum):
    """消息角色。TOOL 用于工具结果回填消息（ADR-0011 §10）。"""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class TurnStatus(Enum):
    """一轮 agent turn 的终态。"""

    COMPLETED = "completed"
    FAILED = "failed"


class TurnPause(Enum):
    """本轮停在了哪种需要人参与的地方. CLI 据此决定驱动哪种交互.

    与 LoopStopReason 分开: 那是"循环为什么停", 这是"该请人做什么". 多个停止原因可能
    映到同一种交互, 而 CLI 不该认识循环的全部停止词汇 —— 它只需要知道该弹哪个界面.
    """

    PLAN_REVIEW = "plan_review"


@dataclass(frozen=True)
class AssistantResponse:
    """一轮对话的助手结果：turn 标识 + 文本 + 终态。

    handle_user_message 的返回值：CLI 只拿它渲染助手那一轮，不关心事件如何落盘
    （落盘在 service 内经 SessionService 完成）。
    """

    turn_id: str
    text: str
    status: TurnStatus
    # 本轮停下来等人做什么. None 表示不需要人参与, 那是绝大多数轮次.
    pause: TurnPause | None = None


@dataclass(frozen=True)
class TurnIdentity:
    """一轮的完整身份 (ADR-0048 决策 2)。

    turn 编号在每个会话里各自从 1 起, 所以 ``turn_0001`` 单独拿出来指不了任何一轮 ——
    两个会话都有一个。凡是要跨会话存放, 传输或展示轮次的地方, 传的都该是这一对。

    与后台调度的 ``run_id`` 是两件事: ``run_id`` 标识"这次后台执行", 由组合根生成;
    这一对标识"会话里的第几轮", 由会话服务生成。互相代用会在恢复历史时对不上。
    """

    session_id: str
    turn_id: str

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("TurnIdentity.session_id 不能为空")
        if not self.turn_id.strip():
            raise ValueError("TurnIdentity.turn_id 不能为空")
