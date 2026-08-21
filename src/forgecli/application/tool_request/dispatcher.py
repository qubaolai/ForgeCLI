"""CoordinatorToolDispatcher: AgentLoop 与安全管线之间的唯一通道.

AgentTurnService 只认识这一个类, 因此它不需要知道 ExecutionContext, PolicyContext 或者
协调器长什么样 —— 也就不可能"顺手"绕开协调器直接调工具.

ADR-0028 删掉了它头上的 ToolDispatcher 抽象: 只有这一个实现, 且实现与抽象同层同文件,
不承担任何依赖倒置. "唯一通道"这条约束由 check_arch.py 的 SIBLING_BANS 守着, 不靠一个
抽象基类表达.

每次调用都重新构造 PolicyContext: mode 可能在两次工具调用之间被用户切换, 而策略上下文
必须反映**发起这次调用时**的模式, 不能沿用一轮开始时的快照.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from forgecli.application.tool_request.coordinator import ToolRequestCoordinator
from forgecli.application.tool_request.observations import ToolObservation
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.agent.actions import ToolRequest
from forgecli.domain.execution.fence import FencePolicy
from forgecli.domain.intents import SessionMode
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.vocabulary import POLICY_VERSION
from forgecli.domain.tool.catalog import ToolCatalog
from forgecli.shared.cancellation import CancelToken

__all__ = ["CoordinatorToolDispatcher"]


class CoordinatorToolDispatcher:
    def __init__(
        self,
        coordinator: ToolRequestCoordinator,
        context_factory: Callable[[], ExecutionContext],
        fence_factory: Callable[[SessionMode], FencePolicy],
        *,
        confined: bool = False,
        interactive: bool = True,
    ) -> None:
        # 每次取一份新的 ExecutionContext: 文件系统视图是带版本的快照, 跨调用复用
        # 就会让第二次调用基于过时的目录内容展开目标.
        self._coordinator = coordinator
        self._context_factory = context_factory
        # 围栏按模式编译, 所以只能在这里挂上 —— context_factory 的其他调用方
        # (`/undo`, Web 展示) 不知道 mode, 也不起子进程 (ADR-0030 决策 4).
        self._fence_factory = fence_factory
        # 围栏是不是真的立起来了. 来自启动期的行为自测, 不是"装了就算".
        self._confined = confined
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
        fence = self._fence_factory(mode)
        context = replace(self._context_factory(), fence=fence)
        policy = PolicyContext(
            mode=mode,
            session_id=session_id,
            turn_id=turn_id,
            execution_profile_hash=context.execution_profile_hash,
            user_intent_summary=user_intent_summary,
            policy_version=POLICY_VERSION,
            interactive=self._interactive,
            fence=fence,
            confined=self._confined,
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
                fence=self._fence_factory(mode),
                confined=self._confined,
            )
        )
