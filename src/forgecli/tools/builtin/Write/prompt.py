"""Write 给模型的说明 (对应 FileWriteTool/prompt.ts)."""

from __future__ import annotations

from collections.abc import Mapping

from forgecli.core.tool.names import FILE_EDIT_TOOL_NAME, FILE_READ_TOOL_NAME

__all__ = ["INPUT_SCHEMA", "DESCRIPTION", "PROMPT"]

DESCRIPTION = "Write a file to the local filesystem."

PROMPT = f"""Writes a file to the local filesystem.

Usage:
- This tool will overwrite the existing file if there is one at the provided path.
- If this is an existing file, you MUST use the {FILE_READ_TOOL_NAME} tool first to read the file's contents. This tool will fail if you did not read the file first.
- Prefer the {FILE_EDIT_TOOL_NAME} tool for modifying existing files - it only sends the diff. Only use this tool to create new files or for complete rewrites.
- NEVER create documentation files (*.md) or README files unless explicitly requested by the User.
- Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked."""


INPUT_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": "The absolute path to the file to write (must be absolute, not relative)",
        },
        "content": {
            "type": "string",
            "description": "The content to write to the file",
        },
    },
    "required": ["file_path", "content"],
    "additionalProperties": False,
}
