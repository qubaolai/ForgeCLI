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
