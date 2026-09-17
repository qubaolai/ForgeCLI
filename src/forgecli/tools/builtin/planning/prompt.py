"""plan_read / plan_write / TodoWrite / todo_set_status 给模型的说明."""

from __future__ import annotations

__all__ = [
    "PLAN_READ_PROMPT",
    "PLAN_WRITE_PROMPT",
    "TODO_SET_STATUS_PROMPT",
    "TODO_WRITE_PROMPT",
]

PLAN_READ_PROMPT = (
    "Read the body of the active plan. Pass plan_id to read a specific one."
)

PLAN_WRITE_PROMPT = """Submit a plan for the user to decide on. Pass plan_id to submit a new version of an existing plan. Give only the structured fields; Forge renders the document from a fixed template.

Todos do not need a plan: building a checklist straight from the request with TodoWrite is the normal case. Use this only when the user has to agree on the overall direction first, and submitting stops the turn to wait for the user's decision - do not treat the plan as approved until that decision arrives."""

TODO_WRITE_PROMPT = """Replace the current todo list with a new one. Use it to fix wrongly split steps, wrong order, or to add and remove items; every status resets to pending. To change statuses only, use todo_set_status.

Build a list before starting when any of these holds: the change touches more than 3 files or several modules; the user's request contains several separately deliverable asks; the work spans stages (change code -> run tests -> update docs); several tool calls have already gone by without converging and you cannot say how many steps remain.

Do not build a list for a small single-file change, a plain question, or a read-only investigation: the list enters the context every turn, and a list for a two-step job is noise.

Once built, keep it current; when it no longer matches reality, rewrite the whole list with this tool."""

TODO_SET_STATUS_PROMPT = """Change the status of one or more todo items. index starts at 0 and matches the numbering printed in the todo list. At most one item may be in_progress at a time.

Set an item to in_progress before starting it, to done as soon as it is finished, and to dropped when it is no longer needed - a list that is not updated stops reflecting the current state of the work."""
