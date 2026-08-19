"""会话事件值对象：追加式审计日志 events.jsonl 的一行。

设计约束（与 domain/intents 一致）：frozen、无副作用；只承载数据与纯转换。
今日只用到 detailed-design §4.2 核心事件的子集（创建 / 模式切换 / 消息 / 斜杠命令）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum


class EventType(Enum):
    """事件类型。值即落盘字符串。"""

    SESSION_CREATED = "session_created"
    # 注释掉：模式改为纯运行时状态后不再写入（见 SessionService.set_mode）
    # MODE_CHANGED = "mode_changed"
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    SLASH_COMMAND = "slash_command"
    # 一次模型调用的 usage 计量摘要（ADR-0011 §11.1）：由 AgentTurnService 写入，
    # payload 为 UsageRecordDraft.to_payload() 的安全摘要，不含凭证 / 原文。
    USAGE_RECORDED = "usage_recorded"

    # -- 工具执行与安全裁决 (ADR-0004 §9 / §15, ADR-0013 §15) --
    #
    # TOOL_REQUESTED 是**写前事件**: 执行之前就要落盘. resume 时有 requested 无
    # completed 的调用结果未知, non_idempotent 的标 outcome_unknown 且不自动重放.
    TOOL_REQUESTED = "tool_requested"
    TOOL_COMPLETED = "tool_completed"
    POLICY_DECISION = "policy_decision"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    CLASSIFIER_INVOKED = "classifier_invoked"

    # -- 工作区恢复 (ADR-0015 §14) --
    CHECKPOINT_CREATED = "checkpoint_created"
    MUTATION_RECORDED = "mutation_recorded"
    RECOVERY_PERFORMED = "recovery_performed"

    # -- 目录授权 (ADR-0014 §7) --
    DIR_GRANT_CHANGED = "dir_grant_changed"

    # -- 计划与待办 (ADR-0022 §6) --
    #
    # 这三条是**审计, 不是重建依据**: 计划与待办的内容真相源是 plans/ 下的文件, payload
    # 只记摘要与引用. 复制一份正文进事件流就有了两个会漂移的副本, 而事件流是 append-only
    # 的, 漂移之后无法修正.
    #
    # 同一 plan_id 的新 revision 也写 PLAN_CREATED 而不是 PLAN_UPDATED: PlanDocument 是
    # frozen 的, 每个 revision 都是一份新文档.
    PLAN_CREATED = "plan_created"
    PLAN_REVIEWED = "plan_reviewed"
    TODO_UPDATED = "todo_updated"


@dataclass(frozen=True)
class SessionEvent:
    """events.jsonl 的一行：通用头 + 业务 payload。append-only，不做原地修改。"""

    event_id: str
    session_id: str
    type: EventType
    created_at: str
    payload: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        """序列化为可 JSON 落盘的纯字典（type 落其字符串值）。"""
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "type": self.type.value,
            "created_at": self.created_at,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> SessionEvent:
        """从一行 JSON 还原事件（供 read / 测试）。"""
        raw_payload = data.get("payload", {})
        payload: Mapping[str, object] = (
            raw_payload if isinstance(raw_payload, Mapping) else {}
        )
        return cls(
            event_id=str(data.get("event_id", "")),
            session_id=str(data.get("session_id", "")),
            type=EventType(str(data.get("type", ""))),
            created_at=str(data.get("created_at", "")),
            payload=payload,
        )
