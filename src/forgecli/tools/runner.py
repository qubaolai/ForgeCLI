"""ToolRunner: 循环与执行链之间的唯一通道 (ADR-0051 的"循环只认识一个入口").

AgentTurnService 只认识这一个类: 它拿一批 ToolCall, 组一份 ToolUseContext, 交给
`run_tools`, 把结果翻成 LoopObservation. 工具池按当前权限上下文现算 (deny 规则会把
整个工具摘掉), 发给模型的 schema 也从这里取.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from forgecli.common.cancellation import CancelToken
from forgecli.core.agent.actions import (
    LoopObservation,
    ObservationDisposition,
    ObservationSource,
)
from forgecli.core.app_state import AppState
from forgecli.core.message import ChatMessage
from forgecli.core.tool.file_state import FileStateCache
from forgecli.core.tool.observer import ToolRunObserver
from forgecli.core.tool.tool import (
    CanUseToolFn,
    PromptOptions,
    Tool,
    ToolUseContext,
    ToolUseOptions,
)
from forgecli.core.tool.tool_call import ToolCall, ToolSchema
from forgecli.human_interaction.service import HumanPromptService
from forgecli.tools.execution import ToolUseOutcome
from forgecli.tools.file_history import FileHistory
from forgecli.tools.hooks import HookRunner
from forgecli.tools.orchestration import run_tools
from forgecli.tools.pool import assemble_tool_pool

__all__ = ["ToolRunner", "observation_of"]


def observation_of(outcome: ToolUseOutcome) -> LoopObservation:
    """把执行链的产出翻成循环的观察. 判据是"谁说的不行": 人拒绝的意图不许换说法重试."""
    disposition = ObservationDisposition.CONTINUE
    source = ObservationSource.TOOL
    if outcome.await_user_decision:
        disposition = ObservationDisposition.AWAIT_USER_DECISION
    elif outcome.user_rejected:
        disposition = ObservationDisposition.HALT
        source = ObservationSource.SECURITY
    elif outcome.status == "denied":
        disposition = ObservationDisposition.BLOCKED
        source = ObservationSource.SECURITY
    elif outcome.status == "error" and not outcome.executed:
        source = ObservationSource.ERROR
    return LoopObservation(
        content=outcome.block.content,
        source=source,
        is_error=outcome.block.is_error,
        disposition=disposition,
    )


@dataclass
class ToolRunner:
    base_tools: tuple[Tool, ...]
    hooks: HookRunner
    can_use_tool: CanUseToolFn
    get_app_state: Callable[[], AppState]
    set_app_state: Callable[[AppState], None]
    prompts: HumanPromptService
    observer: ToolRunObserver
    file_history: FileHistory
    read_file_state: FileStateCache
    cwd: str
    environment: Mapping[str, str]
    workspace_roots: tuple[str, ...]
    tool_results_dir: Callable[[], str]
    internal_paths: tuple[str, ...] = ()
    non_interactive: bool = False
    # 每批调用一个快照 (对应 Claude Code 每条 assistant 消息一个), 编号只在本会话内
    # 有意义.
    batches: int = 0
    _schema_cache: (
        tuple[tuple[tuple[str, ...], bool], tuple[ToolSchema, ...]] | None
    ) = None

    def pool(self) -> tuple[Tool, ...]:
        permission = self.get_app_state().tool_permission_context
        return assemble_tool_pool(self.base_tools, permission)

    def tool_schemas(self) -> tuple[ToolSchema, ...]:
        """发给模型的 schema, 会话内字节锁定 (ADR-0056 决策 8; 对应 toolSchemaCache.ts).

        只在池的成员或沙箱状态变了才重渲染: 工具块排在整条请求最前面, 描述里一个字节
        变了后面全部作废. 池变了由组装器发一条通知告诉模型增减了什么.
        """
        pool = self.pool()
        key = (tuple(tool.name for tool in pool), self.sandboxed())
        if self._schema_cache is not None and self._schema_cache[0] == key:
            return self._schema_cache[1]
        options = PromptOptions(tools=pool, get_app_state=self.get_app_state)
        schemas = tuple(tool.to_model_schema(options) for tool in pool)
        self._schema_cache = (key, schemas)
        return schemas

    def sandboxed(self) -> bool:
        return self.get_app_state().sandbox.active

    def context(
        self,
        *,
        session_id: str,
        turn_id: str,
        messages: tuple[ChatMessage, ...],
        cancel: CancelToken | None,
    ) -> ToolUseContext:
        results_dir = self.tool_results_dir()
        os.makedirs(results_dir, exist_ok=True)
        return ToolUseContext(
            options=ToolUseOptions(
                tools=self.pool(), non_interactive=self.non_interactive
            ),
            abort=cancel if cancel is not None else CancelToken(),
            read_file_state=self.read_file_state,
            messages=messages,
            get_app_state=self.get_app_state,
            set_app_state=self.set_app_state,
            cwd=self.cwd,
            environment=self.environment,
            workspace_roots=self.workspace_roots,
            session_id=session_id,
            turn_id=turn_id,
            prompts=self.prompts,
            observer=self.observer,
            track_file_edit=self.file_history.track_edit,
            tool_results_dir=results_dir,
            internal_paths=self.internal_paths,
        )

    def run(
        self,
        calls: Sequence[ToolCall],
        *,
        session_id: str,
        turn_id: str,
        messages: tuple[ChatMessage, ...] = (),
        parent_message: ChatMessage | None = None,
        cancel: CancelToken | None = None,
    ) -> tuple[ToolUseOutcome, ...]:
        self.observer.bind_turn(session_id, turn_id)
        self.batches += 1
        self.file_history.make_snapshot(f"{turn_id}#{self.batches}")
        context = self.context(
            session_id=session_id, turn_id=turn_id, messages=messages, cancel=cancel
        )
        return run_tools(calls, context, self.can_use_tool, parent_message, self.hooks)
