"""人工 Shell 的应用端口 (ADR-0017 §5).

三个端口, 各自的实现都在别的层:

- ``InteractiveShellProvider``: 平台交互式 Shell 交接. 实现在 infrastructure.
- ``InteractiveShellResolver``: 选哪个 Shell, 用什么参数启动. 实现在 infrastructure ——
  它要看文件存不存在, 可不可执行, 这是平台事实.
- ``TerminalLease``: 终端所有权的唯一协调点. 实现在 interfaces/cli, 因为要暂停的是
  prompt_toolkit 与 Rich Live.

本模块**不 import** ToolRequestCoordinator, ShellTool, ToolAuthorizationService 或任何
Provider SDK (ADR-0017 §13). ``scripts/check_arch.py`` 里有一条互斥规则盯着这件事.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from types import MappingProxyType

from forgecli.domain.manual_shell.request import ManualShellRequest, TerminalSize
from forgecli.domain.manual_shell.result import ManualShellResult

__all__ = [
    "InteractiveShellProvider",
    "InteractiveShellResolver",
    "ManualShellContext",
    "ManualShellObserver",
    "ManualShellUnavailable",
    "TerminalLease",
]


class ManualShellUnavailable(RuntimeError):
    """当前环境起不了交互式 Shell.

    必须携带**可行动**的原因: 用户看到的是这条消息, 而 ADR-0017 §12 要求"显示一条可行动
    错误并返回 Forge, 不进入半初始化状态".
    """


@dataclass(frozen=True)
class ManualShellContext:
    """进入人工 Shell 时 Forge 这边的事实.

    cwd 用 Forge 当前交互上下文的 cwd (默认主工作区根), 而不是上一次人工 Shell 退出时
    的目录 —— 每次进入都是新 Shell, 继承上次的 cwd 会让"我上次 cd 到哪了"变成必须记住
    的隐藏状态 (§6.2).
    """

    cwd: str
    environment: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    terminal: TerminalSize = field(default_factory=TerminalSize)
    # 一次性命令; 空串表示开一次完整会话.
    command: str = ""


class InteractiveShellResolver(ABC):
    """决定启动哪个 Shell (§6.1). 与 Agent 的 executable allowlist 无关, 也不经过
    ToolAuthorizationService —— 用户自己的 Shell 不需要向 Forge 证明身份."""

    @abstractmethod
    def resolve(self, context: ManualShellContext) -> ManualShellRequest:
        """解析出可启动的请求. 找不到可用 Shell 时抛 ManualShellUnavailable."""


class InteractiveShellProvider(ABC):
    """把终端交给一个真实的交互式 Shell, 等它结束, 再交回来."""

    @abstractmethod
    def open(self, request: ManualShellRequest) -> ManualShellResult:
        """阻塞直到子 Shell 结束.

        平台缺少所需终端能力时抛 ManualShellUnavailable, **不允许**降级成
        ``stdin=PIPE`` 的伪交互子进程 (§8.2): 那种"能跑"的降级会丢掉 job control,
        终端探测, 补全和全屏程序, 而用户要过一会儿才发现.
        """


class TerminalLease(ABC):
    """终端所有权的租约. 用上下文管理器表达, 保证 release 走 finally (§7)."""

    @abstractmethod
    def acquire(self) -> AbstractContextManager[None]:
        """暂停 Forge 的输入与渲染, 保存终端状态; 退出时恢复并重绘."""


class ManualShellObserver(ABC):
    """生命周期通知.

    ``entered`` 收到的 request 里带着 ``command`` —— 那是启动子进程必须的, 但实现者
    **不得持久化它** (§9: 生命周期事件不含命令, 输出, history, 环境变量值与子进程参数).
    需要落盘的只有 shell 名, 初始 cwd 与时间.
    """

    @abstractmethod
    def entered(self, request: ManualShellRequest) -> None: ...

    @abstractmethod
    def exited(self, result: ManualShellResult) -> None: ...
