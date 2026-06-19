"""命令处理与绘画状态的基础类型"""

from __future__ import annotations
from abc import ABC
from dataclasses import dataclass

from forgecli.domain.intents import SessionMode, SlashCommand


@dataclass
class SessionState:
    """绘画内可变状态(目前仅内存态)"""

    mode: SessionMode = SessionMode.CHAT
    should_exit: bool = False


class CommandHandler(ABC):
    """斜杠命令处理器 execute 拿到已经解析的 SlashCommand 并执行(可交换类型)"""

    def execute(self, command: SlashCommand) -> None: ...