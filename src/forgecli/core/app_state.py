"""会话级的可变状态.

工具与权限层经 `ToolUseContext.get_app_state()` 读, 经 `set_app_state()` 写; 状态本身
是不可变对象, 每次改都换一份.
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.core.permissions import DenialTrackingState, ToolPermissionContext
from forgecli.core.sandbox import SandboxSettings

__all__ = ["AppState"]


@dataclass(frozen=True)
class AppState:
    tool_permission_context: ToolPermissionContext
    sandbox: SandboxSettings
    # auto 模式下分类器的拦截计数 (ADR-0058 决策 4), 与模式一起住在会话级状态里.
    denial_tracking: DenialTrackingState = DenialTrackingState()
