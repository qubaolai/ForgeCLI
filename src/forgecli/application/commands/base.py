"""命令处理与会话状态的基础类型。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from forgecli.domain.intents import SessionMode, SlashCommand


@dataclass
class SessionState:
    """会话内可变状态；06-23 阶段只保存在内存中。"""

    mode: SessionMode = SessionMode.CHAT
    should_exit: bool = False


class CommandHandler(ABC):
    """斜杠命令处理器，接收已经解析完成的 SlashCommand。"""

    @abstractmethod
    def execute(self, command: SlashCommand) -> None:
        """执行已解析的斜杠命令。"""
