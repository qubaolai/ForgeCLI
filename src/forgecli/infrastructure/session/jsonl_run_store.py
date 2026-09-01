"""处理过程的落盘: ``sessions/<session_id>/runs.jsonl``.

一行一个 turn 的过程快照, 在这一轮收尾时写一次.

## 它不是恢复真相源

ADR-0016 §9 明写运行事件不是恢复依据 —— 会话正文由 `events.jsonl` 提供, 那一份是审计
与重放的事实. 这里存的是**给人看的过程**: 模型每一步说了什么, 调了哪些工具, 各花了多久.

这条分工要守住, 因为它决定了失败时该怎么办: 这个文件损坏, 丢失, 或者根本没写成, 后果
只是历史轮次的处理过程展不开, 会话本身完好. 所以写入路径上的任何异常都吞掉 —— 让一次
磁盘故障中断正在跑的 turn, 是拿真东西去赔一个展示品.

## 为什么在这一层折叠增量

`model_output_delta` 一轮能有几千条. 逐条落盘既撑大文件也让读回来的人再拼一遍, 而它们
的信息量等于拼好的那段正文. 折叠规则与 Web 侧的内存快照完全一致 (同一个形状,
`RunSnapshot`), 前端因此不需要为"历史的"和"进行中的"各写一套渲染.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from forgecli.application.agent_run.events import AgentRunEventSubscriber
from forgecli.domain.agent.run_events import AgentRunEvent, AgentRunEventKind
from forgecli.shared.observability.log import get_log
from forgecli.shared.serialization import to_jsonable

__all__ = ["MAX_STORED_OUTPUT", "JsonlRunStore"]

_log = get_log(__name__)

# 单次模型调用正文的落盘上限. 与 Web 侧的回填上限同一个量级: 再长的正文对"回看过程"
# 也没有增量价值, 而它会让一个长会话的 runs.jsonl 涨到几十 MB.
MAX_STORED_OUTPUT = 8000

# 这几种事件到了就说明这一轮结束了, 该把它写出去.
_TERMINAL = frozenset(
    {
        AgentRunEventKind.TURN_COMPLETED,
        AgentRunEventKind.TURN_FAILED,
        AgentRunEventKind.TURN_CANCELLED,
    }
)


class JsonlRunStore(AgentRunEventSubscriber):
    """按 turn 累积运行事件, 收尾时追加一行.

    在内存里攒而不是每条都写: 一轮几千条事件逐条 append 会让磁盘 IO 进到 Agent 主循环
    的关键路径上. 攒到收尾再写一次, 代价是进程崩了那一轮的过程就没了 —— 而那正好是
    "它不是恢复真相源"这条分工允许的损失.
    """

    def __init__(self, sessions_dir: Path, current_session: Callable[[], str]) -> None:
        self._root = sessions_dir
        # 运行事件信封里没有 session_id (它按 turn 编号, 见 AgentRunEvent), 所以落盘时
        # 现问一次. 传 callable 而不是一个值: 一个进程里会话会换, 而这个订阅者活得比
        # 任何一个会话都长.
        self._current_session = current_session
        # turn_id -> 累积中的快照. OrderedDict 是为了 `_forget_stale` 能从最旧的丢起.
        self._pending: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def on_event(self, event: AgentRunEvent) -> None:
        entry = self._pending.get(event.turn_id)
        if entry is None:
            if event.kind in _TERMINAL:
                # 这一轮已经写过了 (终态可能来两次: 循环自己发一次, 驱动抛异常时
                # AgentTurnService 再补一次). 为它新建一个只有一条事件的条目, 再写出去,
                # 会让读回来的那份**覆盖掉真正有内容的那一行** —— /runs 按 turn_id 去重,
                # 后写的赢.
                return
            entry = {"turn_id": event.turn_id, "events": [], "outputs": {}}
            self._pending[event.turn_id] = entry
            self._forget_stale()
        if event.kind is AgentRunEventKind.MODEL_OUTPUT_DELTA:
            self._fold_delta(entry, event)
        else:
            envelope = to_jsonable(event)
            assert isinstance(envelope, dict)
            entry["events"].append(envelope)
        if event.kind in _TERMINAL:
            self._flush(self._pending.pop(event.turn_id, None))

    def read(self, session_id: str) -> list[dict[str, Any]]:
        """这个会话已经落盘的过程快照, 按写入顺序.

        读不出来就当没有: 一份坏掉的展示数据不该让会话打不开.
        """
        path = self._file(session_id)
        if not path.exists():
            return []
        snapshots: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                if isinstance(item, dict):
                    snapshots.append(item)
        except (OSError, ValueError) as error:
            _log.warning("run_store.read_failed", session=session_id, error=str(error))
            return []
        return snapshots

    # ---- 内部 ----

    def _file(self, session_id: str) -> Path:
        return self._root / session_id / "runs.jsonl"

    def _fold_delta(self, entry: dict[str, Any], event: AgentRunEvent) -> None:
        text = str(getattr(event.payload, "text", "") or "")
        key = event.request_id or ""
        outputs = entry["outputs"]
        outputs[key] = f"{outputs.get(key, '')}{text}"[:MAX_STORED_OUTPUT]

    def _flush(self, entry: dict[str, Any] | None) -> None:
        session_id = self._current_session()
        if entry is None or not session_id:
            return
        try:
            path = self._file(session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except (OSError, ValueError) as error:
            # 吞掉: 让一次磁盘故障中断正在跑的 turn, 是拿真东西去赔一个展示品.
            _log.warning("run_store.write_failed", session=session_id, error=str(error))

    def _forget_stale(self) -> None:
        """没等到收尾事件的轮次不能永远占着内存.

        正常情况下每一轮都会收到终态, 但取消, 崩溃与"事件总线上游异常"都可能让它缺席.
        留三轮的余量, 再多就从最旧的丢 —— 丢掉的是展示数据.
        """
        while len(self._pending) > 3:
            self._pending.popitem(last=False)
