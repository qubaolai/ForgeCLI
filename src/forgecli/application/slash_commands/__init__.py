"""Slash command routing primitives."""

from forgecli.application.slash_commands.base import CommandHandler
from forgecli.application.slash_commands.registry import CommandRegistry, CommandSpec

__all__ = ["CommandHandler", "CommandRegistry", "CommandSpec"]
