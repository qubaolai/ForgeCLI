"""一条斜杠命令能拿到的东西.

刻意不含 REPL 本身: 命令只表达"我要退出"这个意愿 (置 ``exit_requested``), 由 REPL
决定怎么收场. 让命令拿到 REPL 就会出现两条控制流 —— 一条在循环里, 一条在命令里, 而
它们对"现在能不能读下一行输入"这件事迟早会有不同的答案.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console

from forgecli.interfaces.runtime.project_runtime import (
    ProjectRuntime,
    ProjectRuntimeRegistry,
)
from forgecli.interfaces.tui.console import error


class NoActiveProject(RuntimeError):
    """还没有激活的项目. 需要项目的命令据此提前收场, 而不是各判各的 None."""


@dataclass
class CommandContext:
    console: Console
    registry: ProjectRuntimeRegistry
    exit_requested: bool = False

    @property
    def runtime(self) -> ProjectRuntime:
        active = self.registry.active
        if active is None:
            raise NoActiveProject("当前没有激活的项目, 先用 /projects 选一个")
        return active

    def require_idle(self) -> bool:
        """运行配置在 turn 运行期间一律拒绝, 与 Web 同一条边界 (ADR-0025 决策 33)."""
        if self.runtime.busy:
            error(self.console, "这一轮还在跑, 先等它结束或按 Ctrl-C 停止")
            return False
        return True
