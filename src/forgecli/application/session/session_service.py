"""会话用例：把有意义的用户动作落盘为事件 + 快照（惰性创建）。

SessionService 只依赖两个存储抽象与领域值对象，不碰 Rich/Typer/TTY。

惰性落盘：``start()`` 只在内存建立会话身份，不写文件；只有发生第一个「可记录事件」
时才补写 ``session_created`` 并真正建出 events.jsonl / state.json。只进入、不操作
（或只做只读动作）则零文件。

写入顺序（detailed-design §4.3）：先把事件追加进 events.jsonl，再更新 state.json 快照，
保证快照里的 last_event_id 永远指向已落盘的事件。
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime

from forgecli.application.session.event_store import EventStore
from forgecli.application.session.events import EventType, SessionEvent
from forgecli.application.session.snapshot import SessionSnapshot
from forgecli.application.session.state_store import StateStore
from forgecli.domain.conversation import MessageRole, TurnStatus
from forgecli.domain.intents import SessionMode
from forgecli.shared.errors import SessionStateError


def _now_iso() -> str:
    """本地时区、秒级 ISO 时间戳，如 ``2026-06-27T10:30:00+08:00``。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_session_id() -> str:
    """可按时间排序、文件系统安全的 session id，如 ``20260627T103000-ab12cd34``。

    用 ``T`` 分隔且不含 ``:``，兼容 Windows 文件名；尾部随机 hash 避免同秒冲突。
    """
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{secrets.token_hex(4)}"


_TITLE_MAX_LEN = 8


def _title_from(text: str) -> str:
    """从首条输入派生会话摘要名称：取前 ``_TITLE_MAX_LEN`` 字符，超长缀 ``…``。"""
    cleaned = " ".join(text.split())
    if len(cleaned) <= _TITLE_MAX_LEN:
        return cleaned
    return cleaned[:_TITLE_MAX_LEN] + "…"


def _last_seq(events: Sequence[SessionEvent]) -> int:
    """从已有事件推断已用到的最大序号（``evt_0006`` -> 6）；无事件或异常返回 0。"""
    if not events:
        return 0
    try:
        return int(events[-1].event_id.rsplit("_", 1)[-1])
    except (ValueError, IndexError):
        return 0


class SessionService:
    """当前 workspace 的 active session：创建会话身份并记录事件 / 快照。"""

    def __init__(
        self,
        event_store: EventStore,
        state_store: StateStore,
        workspace_root: str,
        *,
        clock: Callable[[], str] = _now_iso,
        id_factory: Callable[[], str] = _new_session_id,
    ) -> None:
        self._events = event_store
        self._states = state_store
        self._root = workspace_root
        self._clock = clock
        self._new_id = id_factory
        self._current: SessionSnapshot | None = None
        self._seq = 0
        self._persisted = False

    def start(self) -> SessionSnapshot:
        """仅在内存建立会话身份（不落盘）。重复调用会开启一个新会话。"""
        snapshot = SessionSnapshot(
            session_id=self._new_id(),
            workspace_root=self._root,
            mode=SessionMode.ACCEPT_EDITS,
            last_event_id=None,
            updated_at=self._clock(),
        )
        self._current = snapshot
        self._seq = 0
        self._persisted = False
        return snapshot

    def current(self) -> SessionSnapshot:
        """当前会话快照（可能尚未落盘）；未 start() 抛 SessionStateError。"""
        if self._current is None:
            raise SessionStateError("会话尚未开始。")
        return self._current

    def resume(
        self, snapshot: SessionSnapshot, events: Sequence[SessionEvent]
    ) -> SessionSnapshot:
        """把活动会话重指向到一段已落盘的历史会话（续写）。

        快照与事件由调用方（ResumeService）一次读出传入。文件已存在，故标记
        ``_persisted=True``：不再补写 session_created，也不再触发 title 设置；
        ``_seq`` 接续历史最后一条事件，后续 record_* 从 ``evt_(_seq+1)`` 追加进同一
        events.jsonl。
        """
        self._current = snapshot
        self._seq = _last_seq(events)
        self._persisted = True
        return snapshot

    def record_user_message(self, text: str, *, turn_id: str) -> SessionEvent:
        """记录一条自然语言输入（属于某个 turn）。"""
        return self._append(
            EventType.USER_MESSAGE,
            {"turn_id": turn_id, "role": MessageRole.USER.value, "text": text},
        )

    def record_assistant_message(
        self,
        text: str,
        *,
        turn_id: str,
        status: TurnStatus,
        stop_reason: str | None = None,
    ) -> SessionEvent:
        """记录一条助手输出（与 user_message 同一 turn 成对，带终态）。

        stop_reason 为循环停止原因的机器可读标记（LoopStopReason 值，如
        user_cancelled）；仅在有值时写入 payload，保证取消轮在事件日志无歧义。
        """
        payload: dict[str, object] = {
            "turn_id": turn_id,
            "role": MessageRole.ASSISTANT.value,
            "status": status.value,
            "text": text,
        }
        if stop_reason is not None:
            payload["stop_reason"] = stop_reason
        return self._append(EventType.ASSISTANT_MESSAGE, payload)

    def record_usage(
        self, payload: Mapping[str, object], *, turn_id: str
    ) -> SessionEvent:
        """记录一次模型调用的 usage 计量摘要（ADR-0011 §11.1）。

        payload 为 UsageRecordDraft.to_payload() 产出的安全摘要；gateway 不落盘，
        写入边界在 AgentTurnService -> 本方法。
        """
        return self._append(EventType.USAGE_RECORDED, {"turn_id": turn_id, **payload})

    def set_mode(self, mode: SessionMode) -> SessionSnapshot:
        """把当前会话切到某个模式：纯内存推进，不写事件、也不触发落盘。

        模式是**运行时状态而非会话史实**：既不进 events.jsonl，也不进 state.json，
        因此每个新会话（含 /resume 续写的会话）都从默认档 accept_edits 起步。两条理由：

        - 快照必须能由事件日志重建（ADR-0001）。若把模式写进 state.json 却不写对应
          事件，就多出一个无从重建的字段，这条不变量即告破。
        - 模式直接决定权限裁决（ADR-0009 决策 6）。昨天升到 full_access，不该在今天
          /resume 时被静默继承——重新授权应当是显式动作。
        """
        self._current = replace(self.current(), mode=mode)
        return self._current

    def record_slash_command(
        self, name: str, args: tuple[str, ...] = ()
    ) -> SessionEvent:
        """记录一次写类型斜杠命令的调用（只读命令不应调用本方法）。"""
        return self._append(
            EventType.SLASH_COMMAND, {"command": name, "args": list(args)}
        )

    def _append(
        self,
        event_type: EventType,
        payload: Mapping[str, object],
        # *,
        # mode: SessionMode | None = None,
    ) -> SessionEvent:
        if self._current is None:
            raise SessionStateError("会话尚未开始。")
        # 会话摘要名称只在首次落盘前、由首条自然语言输入派生一次（之后不改）。
        if (
            not self._persisted
            and event_type == EventType.USER_MESSAGE
            and not self._current.title
        ):
            title = _title_from(str(payload.get("text", "")))
            if title:
                self._current = replace(self._current, title=title)
        self._ensure_persisted()
        # if mode is not None:
        #     self._current = replace(self._current, mode=mode)
        return self._emit(event_type, payload)

    def _ensure_persisted(self) -> None:
        """首个可记录事件触发：补写 session_created，真正建出磁盘文件。"""
        if self._persisted:
            return
        self._persisted = True
        snapshot = self.current()
        # 不带 mode：模式是运行时状态（见 set_mode），落进事件反而会记下"首个可记录
        # 事件发生时的模式"这种既非会话起点、又无人消费的值。
        self._emit(
            EventType.SESSION_CREATED,
            {"workspace_root": snapshot.workspace_root},
        )

    def _emit(
        self, event_type: EventType, payload: Mapping[str, object]
    ) -> SessionEvent:
        snapshot = self.current()
        self._seq += 1
        event_id = f"evt_{self._seq:04d}"
        created_at = self._clock()
        event = SessionEvent(
            event_id=event_id,
            session_id=snapshot.session_id,
            type=event_type,
            created_at=created_at,
            payload=payload,
        )
        # 先追加事件，再更新快照——快照的 last_event_id 始终指向已落盘的事件。
        self._events.append(event)
        self._current = replace(snapshot, last_event_id=event_id, updated_at=created_at)
        self._states.write(self._current)
        return event
