"""ToolDispatcher: AgentLoop 与安全管线之间的唯一通道.

AgentTurnService 只认识这个抽象, 因此它不需要知道 ExecutionContext, PolicyContext 或者
协调器长什么样 —— 也就不可能"顺手"绕开协调器直接调工具.

每次调用都重新构造 PolicyContext: mode 可能在两次工具调用之间被用户切换, 而策略上下文
必须反映**发起这次调用时**的模式, 不能沿用一轮开始时的快照.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from forgecli.application.tool_request.coordinator import ToolRequestCoordinator
from forgecli.application.tool_request.observations import ToolObservation
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.agent.actions import ToolRequest
from forgecli.domain.intents import SessionMode
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.vocabulary import POLICY_VERSION
from forgecli.domain.tool.catalog import ToolCatalog
from forgecli.shared.cancellation import CancelToken

__all__ = ["CoordinatorToolDispatcher", "ToolDispatcher"]


class ToolDispatcher(ABC):
    """把一次工具请求交出去, 拿回一个结构化 observation."""

    @abstractmethod
    def dispatch(
        self,
        request: ToolRequest,
        *,
        mode: SessionMode,
        session_id: str,
        turn_id: str,
        user_intent_summary: str = "",
        cancel: CancelToken | None = None,
    ) -> ToolObservation: ...

    @abstractmethod
    def catalog_for(self, mode: SessionMode) -> ToolCatalog:
        """当前模式下模型可见的工具目录."""


class CoordinatorToolDispatcher(ToolDispatcher):
    def __init__(
        self,
        coordinator: ToolRequestCoordinator,
        context_factory: Callable[[], ExecutionContext],
        *,
        interactive: bool = True,
    ) -> None:
        # 每次取一份新的 ExecutionContext: 文件系统视图是带版本的快照, 跨调用复用
        # 就会让第二次调用基于过时的目录内容展开目标.
        self._coordinator = coordinator
        self._context_factory = context_factory
        self._interactive = interactive

    def dispatch(
        self,
        request: ToolRequest,
        *,
        mode: SessionMode,
        session_id: str,
        turn_id: str,
        user_intent_summary: str = "",
        cancel: CancelToken | None = None,
    ) -> ToolObservation:
        context = self._context_factory()
        policy = PolicyContext(
            mode=mode,
            session_id=session_id,
            turn_id=turn_id,
            execution_profile_hash=context.execution_profile_hash,
            user_intent_summary=user_intent_summary,
            policy_version=POLICY_VERSION,
            interactive=self._interactive,
        )
        return self._coordinator.handle(
            request, context=context, policy=policy, cancel=cancel
        )

    def catalog_for(self, mode: SessionMode) -> ToolCatalog:
        context = self._context_factory()
        return self._coordinator.catalog_for(
            PolicyContext(
                mode=mode,
                session_id="",
                turn_id="",
                execution_profile_hash=context.execution_profile_hash,
            )
        )
