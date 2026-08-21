"""内置工具 (ADR-0004 §14).

每个工具的能力上界都尽量窄: 窄上界让它们在 plan 档目录过滤和确定性快速裁决中受益,
只有 shell.run 的上界最宽, 因此它几乎每次调用都会进能力分析器 —— 那是设计意图,
不是缺陷.
"""

from forgecli.application.tools.builtin.fs_list_files import ListFilesTool
from forgecli.application.tools.builtin.fs_move import MoveTool
from forgecli.application.tools.builtin.fs_read_file import ReadFileTool
from forgecli.application.tools.builtin.fs_scan_tree import ScanTreeTool
from forgecli.application.tools.builtin.fs_write import (
    CreateDirectoryTool,
    CreateFileTool,
    DeleteTool,
    EditFileTool,
)
from forgecli.application.tools.builtin.git_read import GitReadTool
from forgecli.application.tools.builtin.planning_tools import (
    PlanReadTool,
    PlanWriteTool,
    TodoReadTool,
    TodoSetStatusTool,
    TodoWriteTool,
)
from forgecli.application.tools.builtin.search_text import SearchTextTool
from forgecli.application.tools.builtin.shell_run import ShellRunTool

__all__ = [
    "CreateFileTool",
    "CreateDirectoryTool",
    "DeleteTool",
    "EditFileTool",
    "GitReadTool",
    "ListFilesTool",
    "MoveTool",
    "PlanReadTool",
    "PlanWriteTool",
    "ReadFileTool",
    "ScanTreeTool",
    "SearchTextTool",
    "ShellRunTool",
    "TodoReadTool",
    "TodoSetStatusTool",
    "TodoWriteTool",
]
