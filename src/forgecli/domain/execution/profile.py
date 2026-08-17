"""ExecutionProfile: 裁决与授权绑定的执行环境画像 (ADR-0014 §4.1).

安全裁决绑定的不只是"这条命令是什么", 还包括"它会在什么环境里跑". 受控 PATH 变了,
环境净化规则变了, 受保护路径集合变了, 或者隔离等级降级了, 旧的 ALLOW 与待执行审批都必须
失效并重新裁决 —— 否则就会出现"按强隔离批准, 按无隔离执行"这种静默降级.

沙箱层本次未实现, 因此 isolation_level 目前恒为 NO_SANDBOX. 枚举保留三档不是占位癖:
它是授权信封里的绑定项, 将来接入任何隔离方案时, 旧授权应当因为这一项变化而自动失效,
而不是因为有人记得去清缓存.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from forgecli.domain.execution.environment import (
    ENVIRONMENT_SANITIZATION_VERSION,
    ShellLaunch,
)
from forgecli.domain.tool.hashing import digest

__all__ = ["ExecutionProfile", "IsolationLevel"]


class IsolationLevel(Enum):
    """获准进程实际能被限制到什么程度."""

    STRONG_SANDBOX = "strong_sandbox"
    PARTIAL_SANDBOX = "partial_sandbox"
    NO_SANDBOX = "no_sandbox"

    @property
    def contained(self) -> bool:
        return self is not IsolationLevel.NO_SANDBOX


@dataclass(frozen=True)
class ExecutionProfile:
    """一次会话内稳定的执行环境事实."""

    platform: str
    isolation_level: IsolationLevel
    trusted_path: tuple[str, ...]
    shell_launch: ShellLaunch
    environment_allowlist: tuple[str, ...]
    protected_roots_hash: str
    path_normalization_version: str = "1"
    executable_resolution_version: str = "1"
    environment_sanitization_version: str = ENVIRONMENT_SANITIZATION_VERSION
    profile_version: str = "1"

    def __post_init__(self) -> None:
        if not self.trusted_path:
            raise ValueError("ExecutionProfile.trusted_path 不能为空")
        if any(entry in (".", "") for entry in self.trusted_path):
            # PATH 里的 "." 会让"当前目录下有个同名文件"变成一次代码执行.
            raise ValueError("受控 PATH 不能包含当前目录")

    @property
    def trusted_path_hash(self) -> str:
        return digest(self.trusted_path)

    @property
    def execution_profile_hash(self) -> str:
        """授权信封绑定它. 任一字段变化 -> 旧授权失效, 请求重新裁决."""
        return digest(self)
