"""Read 给模型的说明 (对应 FileReadTool/prompt.ts)."""

from __future__ import annotations

from collections.abc import Mapping

from forgecli.core.tool.file_text import MAX_LINES_TO_READ
from forgecli.core.tool.names import BASH_TOOL_NAME

__all__ = ["INPUT_SCHEMA", "DESCRIPTION", "prompt"]

DESCRIPTION = "Read a file from the local filesystem."


def prompt(max_size_label: str) -> str:
    return f"""Reads a file from the local filesystem. You can access any file directly by using this tool.
Assume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid. It is okay to read a file that does not exist; an error will be returned.

Usage:
- The file_path parameter must be an absolute path, not a relative path
- By default, it reads up to {MAX_LINES_TO_READ} lines starting from the beginning of the file. Files larger than {max_size_label} will return an error; use offset and limit for larger files
- When you already know which part of the file you need, only read that part. This can be important for larger files.
- Results are returned using cat -n format, with line numbers starting at 1
- This tool can only read text files, not directories or binary files. To read a directory, use an ls command via the {BASH_TOOL_NAME} tool.
- If you read a file that exists but has empty contents you will receive a system reminder warning in place of file contents."""


INPUT_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": "The absolute path to the file to read",
        },
        "offset": {
            "type": "integer",
            "minimum": 1,
            "description": "The line number to start reading from. Only provide if the file is too large to read at once",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "description": "The number of lines to read. Only provide if the file is too large to read at once.",
        },
    },
    "required": ["file_path"],
    "additionalProperties": False,
}
