"""内置工具 (ADR-0004 §14, ADR-0029 规则三).

七个工具, 五类动作:

    定位  fs.find, search.text
    读取  fs.read, git.read
    写入  fs.apply_patch
    执行  shell.run
    过程  plan.* / todo.*

每个工具的能力上界都尽量窄: 窄上界让它们在 plan 档目录过滤中受益 —— 上界里带写能力
的工具在那一档根本不进 ToolCatalog. 只有 shell.run 的上界最宽, 那是设计意图: 长尾都
走它 (ADR-0029 规则二).
"""

from forgecli.application.tools.builtin.fs_apply_patch import ApplyPatchTool
from forgecli.application.tools.builtin.fs_find import FindTool
from forgecli.application.tools.builtin.fs_read import ReadFileTool
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
    "ApplyPatchTool",
    "FindTool",
    "GitReadTool",
    "PlanReadTool",
    "PlanWriteTool",
    "ReadFileTool",
    "SearchTextTool",
    "ShellRunTool",
    "TodoReadTool",
    "TodoSetStatusTool",
    "TodoWriteTool",
]
