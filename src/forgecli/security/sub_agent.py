"""Restricted state view for report-only child runs (ADR-0062)."""

from collections.abc import Callable
from dataclasses import replace
from threading import Lock

from forgecli.common.observability.log import get_log
from forgecli.core.app_state import AppState
from forgecli.core.permissions import DenialTrackingState, PermissionMode


class SubAgentState:
    def __init__(self, parent: Callable[[], AppState]) -> None:
        self._parent = parent
        self._denials = DenialTrackingState()
        self._lock = Lock()

    def get(self) -> AppState:
        parent = self._parent()
        with self._lock:
            return replace(
                parent,
                tool_permission_context=replace(
                    parent.tool_permission_context,
                    mode=PermissionMode.DEFAULT,
                    should_avoid_permission_prompts=True,
                    always_allow_rules={},
                    pre_plan_mode=None,
                    stripped_dangerous_rules=None,
                ),
                sandbox=replace(parent.sandbox, auto_allow_bash_if_sandboxed=False),
                denial_tracking=self._denials,
            )

    def set(self, state: AppState) -> None:
        current = self.get()
        if (
            state.tool_permission_context != current.tool_permission_context
            or state.sandbox != current.sandbox
        ):
            get_log(__name__).warning("sub_agent.state_change_ignored")
        with self._lock:
            self._denials = state.denial_tracking
