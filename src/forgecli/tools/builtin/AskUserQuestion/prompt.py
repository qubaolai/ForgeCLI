"""AskUserQuestion 给模型的说明."""

from __future__ import annotations

__all__ = ["DESCRIPTION", "PROMPT"]

DESCRIPTION = "Ask the user a question and wait for the answer"

PROMPT = """Ask the user a question and wait for the answer. The turn does not end; once the answer arrives you continue.

Use it only when both hold: the answer changes what you do next, and reading code, reading configuration or running read-only commands cannot produce it. A request with several reasonable readings that lead to different deliverables also qualifies.

Do not use it to ask for permission to run something - Forge asks on its own when approval is needed, and asking here grants nothing. Do not ask for facts that the code answers. Do not ask just to confirm progress or to reassure the user.

Submit the question through the structured tool parameters, never as JSON in a plain reply. selection_mode "single" allows one choice, "multiple" allows several; omitted means single. Every option needs value, a short label and a detail line. recommended_option_id names at most one option's value; omit it when you do not recommend anything. Options are suggestions: the user may type free text or skip the question, and skipping does not mean the recommended option was accepted."""
