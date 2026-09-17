"""把工具链路的运行事件写成结构化日志 (ADR-0035).

原先 ToolRequestCoordinator 每一步都记两遍: 一遍 `_log`, 一遍 observer 事件, 字段大半
相同. 现在协调器只发事件, 日志由这个订阅方从事件里写 —— 一件事一个出口, 加一个字段
只改 payload.

只写工具与审批这几类: 模型调用的日志由循环自己写, 输出增量 (MODEL_OUTPUT_DELTA) 一个
token 一条, 不该进日志.

它在总线上同步执行, 而协调器发事件时正处在 `bind(invocation_id=..., tool=...)` 里,
所以这里写的每一行都自动带着 inv 与 tool.
"""

from __future__ import annotations

from forgecli.agent.run_bus import AgentRunEventSubscriber
from forgecli.agent.run_events import AgentRunEvent, AgentRunEventKind
from forgecli.common.observability.log import get_log
from forgecli.core.serialization import to_jsonable

__all__ = ["RunEventLogger"]

_log = get_log(__name__)

_LOGGED_KINDS = frozenset(
    {
        AgentRunEventKind.TOOL_PREPARED,
        AgentRunEventKind.POLICY_RESOLVED,
        AgentRunEventKind.APPROVAL_REQUESTED,
        AgentRunEventKind.APPROVAL_RESOLVED,
        AgentRunEventKind.TOOL_STARTED,
        AgentRunEventKind.TOOL_COMPLETED,
        AgentRunEventKind.TOOL_CANCELLED,
    }
)


def _is_warning(event: AgentRunEvent) -> bool:
    """没执行, 执行失败, 被拒: 这些进 warning, 其余 info."""
    payload = event.payload
    if event.kind is AgentRunEventKind.TOOL_CANCELLED:
        return True
    if event.kind is AgentRunEventKind.POLICY_RESOLVED:
        return getattr(payload, "decision", "") == "deny"
    if event.kind is AgentRunEventKind.TOOL_COMPLETED:
        if not getattr(payload, "executed", True):
            return True
        return getattr(payload, "status", "") == "error"
    return False


class RunEventLogger(AgentRunEventSubscriber):
    def on_event(self, event: AgentRunEvent) -> None:
        if event.kind not in _LOGGED_KINDS:
            return
        fields = to_jsonable(event.payload)
        if not isinstance(fields, dict):
            fields = {}
        name = "run." + event.kind.value
        if _is_warning(event):
            _log.warning(name, **fields)
        else:
            _log.info(name, **fields)
