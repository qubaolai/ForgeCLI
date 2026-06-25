"""Slash command handler protocol."""

from __future__ import annotations

from abc import ABC, abstractmethod

from forgecli.domain.intents import SlashCommand


class CommandHandler(ABC):
    """斜杠命令处理器，接收已经解析完成的 SlashCommand。"""

    @abstractmethod
    def execute(self, command: SlashCommand) -> bool:
        """执行已解析的斜杠命令, 返回配置是否发生变更。"""
