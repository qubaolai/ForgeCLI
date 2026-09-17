"""Grep 给模型的说明 (对应 GrepTool/prompt.ts)."""

from __future__ import annotations

from collections.abc import Mapping

from forgecli.core.tool.names import BASH_TOOL_NAME, GREP_TOOL_NAME

__all__ = ["INPUT_SCHEMA", "DESCRIPTION"]

DESCRIPTION = f"""A powerful search tool built on ripgrep

  Usage:
  - ALWAYS use {GREP_TOOL_NAME} for search tasks. NEVER invoke `grep` or `rg` as a {BASH_TOOL_NAME} command. The {GREP_TOOL_NAME} tool has been optimized for correct permissions and access.
  - Supports full regex syntax (e.g., "log.*Error", "function\\s+\\w+")
  - Filter files with glob parameter (e.g., "*.js", "**/*.tsx") or type parameter (e.g., "js", "py", "rust")
  - Output modes: "content" shows matching lines, "files_with_matches" shows only file paths (default), "count" shows match counts
  - Pattern syntax: Uses ripgrep (not grep) - literal braces need escaping (use `interface\\{{\\}}` to find `interface{{}}` in Go code)
  - Multiline matching: By default patterns match within single lines only. For cross-line patterns like `struct \\{{[\\s\\S]*?field`, use `multiline: true`
"""


INPUT_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "The regular expression pattern to search for in file contents",
        },
        "path": {
            "type": "string",
            "description": "File or directory to search in (rg PATH). Defaults to current working directory.",
        },
        "glob": {
            "type": "string",
            "description": 'Glob pattern to filter files (e.g. "*.js", "*.{ts,tsx}") - maps to rg --glob',
        },
        "output_mode": {
            "type": "string",
            "enum": ["content", "files_with_matches", "count"],
            "description": (
                'Output mode: "content" shows matching lines (supports -A/-B/-C context, '
                '-n line numbers, head_limit), "files_with_matches" shows file paths '
                '(supports head_limit), "count" shows match counts (supports head_limit). '
                'Defaults to "files_with_matches".'
            ),
        },
        "-B": {
            "type": "integer",
            "description": 'Number of lines to show before each match (rg -B). Requires output_mode: "content", ignored otherwise.',
        },
        "-A": {
            "type": "integer",
            "description": 'Number of lines to show after each match (rg -A). Requires output_mode: "content", ignored otherwise.',
        },
        "-C": {"type": "integer", "description": "Alias for context."},
        "context": {
            "type": "integer",
            "description": 'Number of lines to show before and after each match (rg -C). Requires output_mode: "content", ignored otherwise.',
        },
        "-n": {
            "type": "boolean",
            "description": 'Show line numbers in output (rg -n). Requires output_mode: "content", ignored otherwise. Defaults to true.',
        },
        "-i": {"type": "boolean", "description": "Case insensitive search (rg -i)"},
        "type": {
            "type": "string",
            "description": "File type to search (rg --type). Common types: js, py, rust, go, java, etc. More efficient than include for standard file types.",
        },
        "head_limit": {
            "type": "integer",
            "description": (
                'Limit output to first N lines/entries, equivalent to "| head -N". Works '
                "across all output modes: content (limits output lines), files_with_matches "
                "(limits file paths), count (limits count entries). Defaults to 250 when "
                "unspecified. Pass 0 for unlimited (use sparingly - large result sets waste "
                "context)."
            ),
        },
        "offset": {
            "type": "integer",
            "description": 'Skip first N lines/entries before applying head_limit, equivalent to "| tail -n +N | head -N". Works across all output modes. Defaults to 0.',
        },
        "multiline": {
            "type": "boolean",
            "description": "Enable multiline mode where . matches newlines and patterns can span lines (rg -U --multiline-dotall). Default: false.",
        },
    },
    "required": ["pattern"],
    "additionalProperties": False,
}
