"""CommandExecutor: 启动子进程的唯一抽象.

这是将来接入任何隔离方案的**唯一接缝**. 沙箱本次不实现, 当前只有
infrastructure/execution/local_command_executor.py 一个实现; 之后无论换成 SRT, 容器
还是远程执行节点, 都从这里接入, 而不是从 shell.run 或 git.read 内部接入 —— 工具一旦
自己知道"有沙箱时走这条, 没沙箱时走那条", 就会出现绕过隔离的隐藏分支.

请求里带的是**绝对可执行文件路径与已净化的环境**: 执行器不做 PATH 查找, 不继承
os.environ. 那两件事都会让"裁决时解析的可执行文件"与"实际跑起来的可执行文件"分叉.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field

from forgecli.shared.cancellation import CancelToken

__all__ = ["CommandExecutor", "CommandRequest", "CommandOutcome"]


@dataclass(frozen=True)
class CommandRequest:
    argv: tuple[str, ...]
    cwd: str
    environment: Mapping[str, str]
    timeout_seconds: float
    max_output_bytes: int
    stdin: str | None = None

    def __post_init__(self) -> None:
        if not self.argv:
            raise ValueError("CommandRequest.argv 不能为空")
        if self.timeout_seconds <= 0:
            raise ValueError("CommandRequest.timeout_seconds 必须为正")


@dataclass(frozen=True)
class CommandOutcome:
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False
    truncated: bool = False
    duration_seconds: float = 0.0
    child_process_count: int = 0
    failure: str | None = field(default=None)

    @property
    def succeeded(self) -> bool:
        return (
            self.exit_code == 0
            and not self.timed_out
            and not self.cancelled
            and self.failure is None
        )


class CommandExecutor(ABC):
    """启动并回收一个子进程."""

    @abstractmethod
    def run(
        self, request: CommandRequest, cancel: CancelToken | None = None
    ) -> CommandOutcome:
        """执行到结束, 超时或取消. 返回时子进程及其进程组必须已经回收."""
