"""沙箱设置 (ADR-0055 决策 7; 对应 Claude Code 的 settings.sandbox).

围栏 (`core/execution/fence.py`) 是实现, 这里是驱动它的设置. 与模式正交: 模式说"什么
时候问人", 沙箱说"子进程能碰什么".
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SandboxSettings"]


@dataclass(frozen=True)
class SandboxSettings:
    enabled: bool = True
    # 围栏立起来的 Bash 命令自动放行 (先查 deny / ask 规则). 原 auto 预设就是它.
    auto_allow_bash_if_sandboxed: bool = False
    # 含这些前缀的命令不进围栏 (`docker *` 一类).
    excluded_commands: tuple[str, ...] = ()
    # 模型能否用 dangerously_disable_sandbox 跳出围栏.
    allow_unsandboxed_commands: bool = False
    network_allowed: bool = False
    # 围栏之外额外可写的路径 (settings.sandbox.filesystem.allowWrite).
    allow_write: tuple[str, ...] = ()
    # 围栏实际立没立起来: 启动期的 Provider 自测, 不是用户配置. 没立起来等于
    # enabled=False, 自动放行不生效.
    confined: bool = False

    @property
    def active(self) -> bool:
        return self.enabled and self.confined
