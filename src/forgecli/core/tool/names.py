"""内置工具的名字与曾用名 (ADR-0054 决策 2).

名字用 Claude Code 的: 权限规则 `Bash(git commit:*)` 与它的设置文件逐字相同, 模型对
这几个名字与入参形状也已经很熟. 曾用名只用于查找 (旧会话记录, 旧规则文件), 不进目录.
"""

from __future__ import annotations

import re

__all__ = [
    "ASK_USER_QUESTION_TOOL_NAME",
    "BASH_TOOL_NAME",
    "FILE_EDIT_TOOL_NAME",
    "FILE_READ_TOOL_NAME",
    "FILE_WRITE_TOOL_NAME",
    "GLOB_TOOL_NAME",
    "GREP_TOOL_NAME",
    "LEGACY_TOOL_NAMES",
    "LSP_TOOL_NAME",
    "MEMORY_FORGET_TOOL_NAME",
    "MEMORY_WRITE_TOOL_NAME",
    "PLAN_READ_TOOL_NAME",
    "PLAN_WRITE_TOOL_NAME",
    "TODO_SET_STATUS_TOOL_NAME",
    "TODO_WRITE_TOOL_NAME",
    "is_valid_tool_name",
    "normalize_legacy_tool_name",
]

FILE_READ_TOOL_NAME = "Read"
FILE_EDIT_TOOL_NAME = "Edit"
FILE_WRITE_TOOL_NAME = "Write"
GLOB_TOOL_NAME = "Glob"
GREP_TOOL_NAME = "Grep"
LSP_TOOL_NAME = "LSP"
BASH_TOOL_NAME = "Bash"
TODO_WRITE_TOOL_NAME = "TodoWrite"
ASK_USER_QUESTION_TOOL_NAME = "AskUserQuestion"
# 本仓库自有的几个 (计划评审与跨会话记忆), 提示词里要点名, 所以与上面同住一处.
PLAN_READ_TOOL_NAME = "plan_read"
PLAN_WRITE_TOOL_NAME = "plan_write"
TODO_SET_STATUS_TOOL_NAME = "todo_set_status"
MEMORY_WRITE_TOOL_NAME = "memory_write"
MEMORY_FORGET_TOOL_NAME = "memory_forget"
TEST_TOOL_NAME = "TEST"

# 曾用名 -> 现在的名字. 规则解析 (security) 与工具池 (tools) 共用这一张.
LEGACY_TOOL_NAMES: dict[str, str] = {
    "fs_read": FILE_READ_TOOL_NAME,
    "file_read": FILE_READ_TOOL_NAME,
    "file_edit": FILE_EDIT_TOOL_NAME,
    "file_write": FILE_WRITE_TOOL_NAME,
    "fs_apply_patch": FILE_EDIT_TOOL_NAME,
    "glob": GLOB_TOOL_NAME,
    "grep": GREP_TOOL_NAME,
    "search_text": GREP_TOOL_NAME,
    "lsp": LSP_TOOL_NAME,
    "shell_run": BASH_TOOL_NAME,
    "todo_write": TODO_WRITE_TOOL_NAME,
    "ask_user": ASK_USER_QUESTION_TOOL_NAME,
}

_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def is_valid_tool_name(name: str) -> bool:
    return _NAME_PATTERN.match(name) is not None


def normalize_legacy_tool_name(name: str) -> str:
    return LEGACY_TOOL_NAMES.get(name, name)
