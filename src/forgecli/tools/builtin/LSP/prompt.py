"""LSP 给模型的说明 (对应 LSPTool/prompt.ts 的要点)."""

from __future__ import annotations

from collections.abc import Mapping

from forgecli.core.tool.names import FILE_READ_TOOL_NAME, GREP_TOOL_NAME
from forgecli.tools.builtin.LSP.operations import OPERATIONS

__all__ = ["INPUT_SCHEMA", "DESCRIPTION", "PROMPT"]

DESCRIPTION = "Code intelligence via a language server"

PROMPT = f"""Interact with a Language Server Protocol (LSP) server to get code intelligence features: go to definition, find references, hover information, document symbols, workspace symbols, go to implementation, and call hierarchy.

Use this instead of {GREP_TOOL_NAME} when you need semantic answers: "who calls getUserById" via findReferences or incomingCalls resolves renames, aliases and same-named symbols in other modules correctly, which text search cannot.

Operations:
- goToDefinition: jump to where a symbol is defined
- findReferences: list every reference to the symbol at a position
- hover: type signature and documentation at a position
- documentSymbol: outline of the symbols in a file
- workspaceSymbol: search symbols across the workspace by name (requires query; filePath is still needed to pick the language)
- goToImplementation: implementations of an interface or abstract method
- prepareCallHierarchy: the call-hierarchy item at a position
- incomingCalls: who calls the function at a position
- outgoingCalls: what the function at a position calls

line and character are 1-based and match the line numbers shown by {FILE_READ_TOOL_NAME}; point them at any character inside the symbol's name. A language server for the file's language must be installed (pyright, typescript-language-server, gopls, rust-analyzer, clangd, jdtls); if it is not, the tool says so and you should fall back to {GREP_TOOL_NAME}."""


INPUT_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": list(OPERATIONS),
            "description": "The LSP operation to perform",
        },
        "filePath": {
            "type": "string",
            "description": "The absolute or relative path to the file",
        },
        "line": {
            "type": "integer",
            "minimum": 1,
            "description": "The line number (1-based)",
        },
        "character": {
            "type": "integer",
            "minimum": 1,
            "description": "The character offset (1-based), pointing inside the symbol name",
        },
        "query": {"type": "string", "description": "Search query for workspaceSymbol"},
    },
    "required": ["operation", "filePath"],
    "additionalProperties": False,
}
