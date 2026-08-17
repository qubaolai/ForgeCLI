"""ManualShellRequest: 启动一次人工 Shell 所需的全部事实 (ADR-0017 §5).

与 Agent 侧的 ``ShellLaunch`` 是**两个东西**, 不要合并:

- ``ShellLaunch`` 描述的是非交互, 非登录, 环境已净化的受控子进程 —— 那是给模型提出的
  命令用的.
- 这里描述的是用户自己的交互式 Shell: 走 ``-i``, 继承用户环境快照, 读用户的 rc 文件.

``environment`` 是**用户环境快照**而不是 ADR-0014 的净化结果 (§6.3). 净化是为了让
Agent 执行的命令可预测; 人工 Shell 的可预测性由用户自己负责, 净化过的环境反而会让
他熟悉的 alias, 虚拟环境和 PATH 全部消失.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

__all__ = ["ManualShellRequest", "TerminalSize"]


@dataclass(frozen=True)
class TerminalSize:
    """终端尺寸. 交给子 Shell 用于初始布局; 之后的变化由 SIGWINCH 自己传."""

    columns: int = 80
    rows: int = 24

    def __post_init__(self) -> None:
        if self.columns <= 0 or self.rows <= 0:
            raise ValueError("终端尺寸必须为正")


@dataclass(frozen=True)
class ManualShellRequest:
    shell_executable: str
    argv: tuple[str, ...]
    cwd: str
    environment: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    terminal: TerminalSize = field(default_factory=TerminalSize)
    # 一次性命令 (`# clear`). 空串表示交互式会话 (`#`).
    #
    # 它在这里是因为**非有不可**: 不放进 argv 就跑不了. 但它绝不能流向审计, 事件或
    # 模型上下文 —— 见 ManualShellResult 的注释, 那边一个能装命令的字段都没有.
    command: str = ""

    def __post_init__(self) -> None:
        if not self.shell_executable.strip():
            raise ValueError("ManualShellRequest.shell_executable 不能为空")
        if not self.argv:
            raise ValueError("argv 至少要有 argv[0]")
        if not self.cwd.strip():
            raise ValueError("ManualShellRequest.cwd 不能为空")

    @property
    def interactive(self) -> bool:
        """是不是要开一次完整会话.

        两者在界面上的差别不是装饰: 一次性命令**不能**打边界提示 —— `# clear` 之后再
        打一行"返回 Forge"就把刚清掉的屏幕又占上了, 而用户要的正是干净的屏幕.
        """
        return not self.command

    @property
    def descriptor(self) -> str:
        """给用户看的一行摘要. 只有 Shell 名, 不含命令与环境变量值."""
        return self.shell_executable.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
