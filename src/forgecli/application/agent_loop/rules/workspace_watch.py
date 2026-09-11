"""工作区文件在本轮里被谁改了, 改了就告诉模型 (ADR-0047).

四个时机都要看一眼, 因为变化归谁取决于它发生在哪一段:

- 调模型之前, 派工具之前: 没有 agent 工具在跑, 变化算外部参与者的.
- 拿到工具结果之后: 上一个检查点在派发前, 这段差异属于刚跑完的那个工具, 包括 shell
  这类无法在结果里列出具体改了哪些路径的工具.
- 模型回来之后: 生成期间也可能有人改文件. 如果这次本来要直接回答, 不能把一个基于旧
  文件状态的答案当成最终答案 —— 追加事实, 重新问一次.

攒着的变化在下一次调模型之前一并告诉模型, 不在两条工具结果中间插.
"""

from __future__ import annotations

from forgecli.application.agent_loop.model_invoker import ModelOutcome
from forgecli.application.agent_loop.rule import (
    AfterModelVerdict,
    AfterObserveVerdict,
    BeforeDispatchVerdict,
    BeforeModelVerdict,
    LoopView,
)
from forgecli.application.agent_loop.verdicts import Continue, Reask, Rewrite
from forgecli.application.prompt.template_renderer import render_notice
from forgecli.application.workspace.monitor import (
    WorkspaceChangeMonitor,
    WorkspaceSnapshotProvider,
)
from forgecli.domain.agent.actions import LoopObservation
from forgecli.domain.conversation.message import ChatMessage, TextBlock
from forgecli.domain.conversation.turn import MessageRole
from forgecli.domain.tool.tool_call import ToolCall
from forgecli.domain.workspace.changes import WorkspaceChange, WorkspaceChangeSource
from forgecli.shared.observability.log import get_log

__all__ = ["WorkspaceWatchRule"]

_log = get_log(__name__)


class WorkspaceWatchRule:
    def __init__(self, provider: WorkspaceSnapshotProvider | None) -> None:
        # 没接快照就什么都不看. 第一次检查点只拍基线, 不产生变化.
        self._monitor = None if provider is None else WorkspaceChangeMonitor(provider)
        self._pending: list[WorkspaceChange] = []

    def before_model(self, view: LoopView) -> BeforeModelVerdict:
        self._checkpoint(WorkspaceChangeSource.EXTERNAL)
        notice = self._take_notice(view)
        if notice is None:
            return Continue()
        return Rewrite(view.window.append(notice))

    def after_model(self, outcome: ModelOutcome, view: LoopView) -> AfterModelVerdict:
        self._checkpoint(WorkspaceChangeSource.EXTERNAL)
        if outcome.tool_calls:
            # 还要跑工具, 变化攒着, 下一次调模型之前一并说.
            return Continue()
        notice = self._take_notice(view)
        if notice is None:
            return Continue()
        return Reask(messages=(notice,))

    def before_dispatch(self, call: ToolCall, view: LoopView) -> BeforeDispatchVerdict:
        self._checkpoint(WorkspaceChangeSource.EXTERNAL)
        return Continue()

    def after_observe(
        self, call: ToolCall, observation: LoopObservation, view: LoopView
    ) -> AfterObserveVerdict:
        self._checkpoint(WorkspaceChangeSource.AGENT)
        return Continue()

    # ---- 内部 ----

    def _checkpoint(self, source: WorkspaceChangeSource) -> None:
        if self._monitor is None:
            return
        self._pending.extend(self._monitor.checkpoint(source))

    def _take_notice(self, view: LoopView) -> ChatMessage | None:
        """把攒着的变化写成一条通知并报出去; 没有变化就 None."""
        if not self._pending:
            return None
        changes = tuple(self._pending)
        self._pending.clear()
        view.ledger.report_workspace_changes(changes)
        _log.info(
            "workspace.changed",
            changes=[
                {
                    "path": change.path,
                    "kind": change.kind.value,
                    "source": change.source.value,
                }
                for change in changes
            ],
        )
        notice = render_notice("loop.workspace_changed", changes=changes)
        return ChatMessage(role=MessageRole.USER, content=(TextBlock(notice),))
