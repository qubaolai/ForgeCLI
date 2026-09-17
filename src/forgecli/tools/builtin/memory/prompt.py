"""memory_write / memory_forget 给模型的说明 (ADR-0033 决策 9)."""

from __future__ import annotations

__all__ = ["FORGET_PROMPT", "SCOPE_DESCRIPTION", "WRITE_PROMPT"]

SCOPE_DESCRIPTION = (
    "project = facts about this project (test command, build command, what a "
    "directory means, architecture facts); user = preferences that follow the user "
    "(language, output style)."
)

WRITE_PROMPT = """Record a fact that will still hold after this conversation ends, for later sessions.

Only record verifiable facts and preferences the user stated explicitly; never record inferences, guesses, progress or decisions (those belong in the plan), and never record credentials. Writing the same key again overwrites the old value.

Do not record what the repository already records: directory structure, where a function lives, what changed this time - the next session reads it again. Do not record things that only hold within this conversation.

Before recording, check whether the same fact is already recorded: if so, update that entry with the same key instead of adding a parallel one."""

FORGET_PROMPT = """Delete a memory that no longer holds. Use it when a memory contradicts the current facts."""
