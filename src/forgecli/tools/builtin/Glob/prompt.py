"""Glob 给模型的说明 (对应 GlobTool/prompt.ts)."""

from __future__ import annotations

from collections.abc import Mapping

__all__ = ["INPUT_SCHEMA", "DESCRIPTION"]

DESCRIPTION = """- Fast file pattern matching tool that works with any codebase size
- Supports glob patterns like "**/*.js" or "src/**/*.ts"
- Returns matching file paths sorted by modification time
- Use this tool when you need to find files by name patterns
- When you are doing an open ended search that may require multiple rounds of globbing and grepping, combine it with Grep instead of guessing paths"""


INPUT_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "The glob pattern to match files against",
        },
        "path": {
            "type": "string",
            "description": (
                "The directory to search in. If not specified, the current working "
                "directory will be used. IMPORTANT: Omit this field to use the default "
                'directory. DO NOT enter "undefined" or "null" - simply omit it for the '
                "default behavior. Must be a valid directory path if provided."
            ),
        },
    },
    "required": ["pattern"],
    "additionalProperties": False,
}
