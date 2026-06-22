"""In-memory session state for the current CLI shell slice."""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.intents import SessionMode


@dataclass
class SessionState:
    """Mutable REPL state before persistent session storage is introduced."""

    mode: SessionMode = SessionMode.CHAT
    should_exit: bool = False
