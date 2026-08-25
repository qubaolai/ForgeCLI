"""MemoryService: 记忆对外的唯一入口 (ADR-0033 决策 10).

**只注入 AgentTurnService 与记忆工具, 不注入 AgentLoop.** ADR-0010 §影响明写循环
"不能直接写文件, 执行 shell, 写事件或读写长期记忆"; 把入口给它是字面违反. 功能上也
用不着 —— 记忆是提示词块, 按 ADR-0018 每轮冻结, 读取只发生在 `_compile_prompt` 那
一刻, 与 ProjectInstructionReader 和 PlanningService 完全同构. 于是循环对记忆的依赖
数是 0, 比给它一个端口解耦得更彻底.

## 为什么服务自己持有"当前是哪一轮"

来源要记 session_id 与 turn_id (决策 7), 而工具的 `perform` 只拿得到 ToolPlan 与
ExecutionContext —— 两者都不带会话身份. 把它们塞进 `normalized_input` 是能拿到, 但
那会进 `plan_hash`, 于是"两次内容完全相同的调用得到同一个 plan_hash"这条不变量就破了
(见 `ToolPlan._hash_source` 的注释).

所以由 AgentTurnService 在每轮开始时调 `begin_turn`. 形状与 ProjectContext 一样: 一个
进程内可变的持有者, 由唯一的驱动方推进.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from forgecli.application.memory.memory_store import (
    MAX_ENTRIES_PER_SCOPE,
    MAX_VALUE_BYTES,
    MemoryStore,
)
from forgecli.domain.memory.entry import (
    KEY_PATTERN,
    MemoryEntry,
    MemoryProvenance,
    MemoryScope,
)
from forgecli.domain.memory.secrets import looks_like_secret
from forgecli.shared.utils import now_iso

__all__ = ["MemoryRejection", "MemoryService", "MemoryWrite"]


class MemoryRejection(Enum):
    """写入被拒的原因. 值进 observation, 模型据此决定改哪里."""

    INVALID_KEY = "invalid_key"
    EMPTY_VALUE = "empty_value"
    VALUE_TOO_LONG = "value_too_long"
    LOOKS_LIKE_SECRET = "looks_like_secret"
    SCOPE_FULL = "scope_full"


@dataclass(frozen=True)
class MemoryWrite:
    """一次写入的结果. rejection 为 None 表示写成功了."""

    rejection: MemoryRejection | None = None
    # 覆盖掉的旧值 (决策 6). 空表示这是一条新记忆.
    replaced: str = ""

    @property
    def accepted(self) -> bool:
        return self.rejection is None


class MemoryService:
    def __init__(
        self,
        stores: dict[MemoryScope, MemoryStore],
        *,
        clock: Callable[[], str] = now_iso,
    ) -> None:
        self._stores = stores
        self._clock = clock
        self._session_id = ""
        self._turn_id = ""

    def begin_turn(self, *, session_id: str, turn_id: str) -> None:
        """记下当前是哪一轮, 供本轮内的写入取来源."""
        self._session_id = session_id
        self._turn_id = turn_id

    def load(self) -> tuple[MemoryEntry, ...]:
        """全部记忆, 项目事实在前, 用户偏好在后.

        顺序固定而不是按写入时间: 提示词必须是输入相同就字节相同的 (ADR-0018 §2.2),
        而两个 store 各自的读出顺序合起来会随写入历史变.
        """
        merged: list[MemoryEntry] = []
        for scope in (MemoryScope.PROJECT, MemoryScope.USER):
            store = self._stores.get(scope)
            if store is None:
                continue
            merged.extend(sorted(store.load(), key=lambda entry: entry.key))
        return tuple(merged)

    def remember(
        self, scope: MemoryScope, key: str, value: str, *, derived_from: str = ""
    ) -> MemoryWrite:
        """记一条. 同 key 直接覆盖 (决策 6)."""
        store = self._stores.get(scope)
        if store is None:
            return MemoryWrite(MemoryRejection.SCOPE_FULL)
        rejection = self._reject(key, value)
        if rejection is not None:
            return MemoryWrite(rejection)
        existing = store.load()
        replaced = next((e.value for e in existing if e.key == key), "")
        kept = tuple(entry for entry in existing if entry.key != key)
        if not replaced and len(kept) >= MAX_ENTRIES_PER_SCOPE:
            return MemoryWrite(MemoryRejection.SCOPE_FULL)
        store.save(
            (
                *kept,
                MemoryEntry(
                    key=key,
                    value=value.strip(),
                    scope=scope,
                    provenance=MemoryProvenance(
                        session_id=self._session_id,
                        turn_id=self._turn_id,
                        created_at=self._clock(),
                        derived_from=derived_from.strip(),
                    ),
                ),
            )
        )
        return MemoryWrite(replaced=replaced)

    def forget(self, scope: MemoryScope, key: str) -> bool:
        """删一条. 返回它本来在不在 —— 删一个不存在的 key 不是错误, 但模型该知道."""
        store = self._stores.get(scope)
        if store is None:
            return False
        existing = store.load()
        kept = tuple(entry for entry in existing if entry.key != key)
        if len(kept) == len(existing):
            return False
        store.save(kept)
        return True

    def _reject(self, key: str, value: str) -> MemoryRejection | None:
        if not KEY_PATTERN.match(key):
            return MemoryRejection.INVALID_KEY
        if not value.strip():
            return MemoryRejection.EMPTY_VALUE
        if len(value.encode("utf-8")) > MAX_VALUE_BYTES:
            return MemoryRejection.VALUE_TOO_LONG
        # 凭证检查放在最后, 但它是这一组里唯一一条**安全**判据 (决策 5): 静默写入意味着
        # 一条 API key 可能被无声写进磁盘, 并在此后每一轮进提示词.
        if looks_like_secret(value) or looks_like_secret(key):
            return MemoryRejection.LOOKS_LIKE_SECRET
        return None
