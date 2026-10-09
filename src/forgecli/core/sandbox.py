"""沙箱设置 (ADR-0055 决策 7.

围栏 (`core/execution/fence.py`) 是实现, 这里是驱动它的设置. 与模式正交: 模式说"什么
时候问人", 沙箱说"子进程能碰什么".

缺省值: 不开沙箱; 开了之后围栏内的 Bash 自动放行 (沙箱换来的就是
不用问), 模型可以用 `dangerously_disable_sandbox` 跳出围栏 (会触发询问).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SandboxSettings"]


@dataclass(frozen=True)
class SandboxSettings:
    enabled: bool = True
    # 围栏立起来的 Bash 命令自动放行 (先查 deny / ask 规则).
    auto_allow_bash_if_sandboxed: bool = True
    # 含这些前缀的命令不进围栏 (`docker *` 一类). 不是安全边界.
    excluded_commands: tuple[str, ...] = ()
    # 模型能否用 dangerously_disable_sandbox 跳出围栏.
    allow_unsandboxed_commands: bool = True
    # ---- 网络 (settings.sandbox.network) ----
    # 只有这些域名能经代理出去. `*` 表示全部; `*.example.com` 表示严格子域; 空 = 全拦
    # (有人在场时不认识的域名会问一次).
    allowed_domains: tuple[str, ...] = ()
    denied_domains: tuple[str, ...] = ()
    # macOS: 允许连接 / 监听这些 unix socket 路径 (subpath). Linux 的 seccomp 按不了
    # 路径.
    allow_unix_sockets: tuple[str, ...] = ()
    allow_all_unix_sockets: bool = False
    # 允许绑定本地端口与连本机 (dev server 一类).
    allow_local_binding: bool = False
    # ---- 文件系统 (settings.sandbox.filesystem) ----
    allow_write: tuple[str, ...] = ()
    deny_write: tuple[str, ...] = ()
    deny_read: tuple[str, ...] = ()
    # denyRead 区域里再放开的路径, 优先于 denyRead.
    allow_read: tuple[str, ...] = ()
    # 围栏实际立没立起来: 启动期的 Provider 自测, 不是用户配置. 没立起来等于
    # enabled=False, 自动放行不生效.
    confined: bool = False

    @property
    def active(self) -> bool:
        return self.enabled and self.confined

    @property
    def network_unrestricted(self) -> bool:
        """`allowedDomains` 里有 `*`: 不经代理, 围栏直接放开网络."""
        return "*" in self.allowed_domains
