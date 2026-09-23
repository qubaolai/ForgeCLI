"""执行链发布事实的出口 (ADR-0016). 实现在 agent/ (发到运行事件总线), 这里只有形状.

执行链 (tools/execution.py) 只认识这个 ABC, 认识不到事件总线与界面.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

__all__ = ["ToolRunObserver"]


class ToolRunObserver(ABC):
    @abstractmethod
    def bind_turn(self, session_id: str, turn_id: str, agent_id: str = "") -> None: ...

    @abstractmethod
    def tool_validated(
        self,
        tool_name: str,
        *,
        tool_use_id: str,
        activity: str,
        arguments: tuple[tuple[str, str], ...],
        elapsed_ms: float,
    ) -> None:
        """schema 与 validate_input 都过了, 入参已经是工具听成的那一份."""

    @abstractmethod
    def permission_decided(
        self,
        tool_name: str,
        *,
        tool_use_id: str,
        behavior: str,
        reason: str,
        message: str,
        elapsed_ms: float,
    ) -> None: ...

    @abstractmethod
    def permission_prompted(
        self,
        tool_name: str,
        *,
        tool_use_id: str,
        prompt_id: str,
        message: str,
        suggestions: tuple[str, ...],
    ) -> None: ...

    @abstractmethod
    def permission_answered(
        self,
        tool_name: str,
        *,
        tool_use_id: str,
        prompt_id: str,
        outcome: str,
        choice: str,
        note: str,
    ) -> None: ...

    @abstractmethod
    def tool_started(self, tool_name: str, *, tool_use_id: str) -> None: ...

    @abstractmethod
    def tool_completed(
        self,
        tool_name: str,
        *,
        tool_use_id: str,
        status: str,
        elapsed_ms: float,
        result_summary: str,
        error_summary: str,
        bytes_out: int,
        persisted: bool,
        executed: bool,
    ) -> None: ...

    @abstractmethod
    def tool_cancelled(
        self, tool_name: str, *, tool_use_id: str, elapsed_ms: float
    ) -> None: ...
