"""ExecutionProfile: 裁决与授权绑定的执行环境画像 (ADR-0014 §4.1, ADR-0030 决策 3).

安全裁决绑定的不只是"这条命令是什么", 还包括"它会在什么环境里跑". 受控 PATH 变了,
环境净化规则变了, 受保护路径集合变了, 或者围栏自测结论变了, 旧的 ALLOW 与待执行审批
都必须失效并重新裁决 —— 否则就会出现"按有围栏批准, 按无围栏执行"这种静默降级.

`isolation_level` 由启动时的**行为自测**填, 不由平台判断填: 探测的方式是真的去做一次
被禁止的操作, 验证它确实失败 (ADR-0030 决策 3). 自测任意一项不过就是 UNCONFINED.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from forgecli.domain.execution.environment import (
    ENVIRONMENT_SANITIZATION_VERSION,
    EnvironmentInheritance,
    ShellLaunch,
)
from forgecli.domain.tool.hashing import digest
from forgecli.domain.workspace.boundary import PATH_NORMALIZATION_VERSION

__all__ = ["ExecutionProfile", "IsolationLevel"]

PROFILE_VERSION = "2"


class IsolationLevel(Enum):
    """围栏自测的结论: 子进程是不是真的被关住了 (ADR-0030 决策 3).

    只有两档. 原先的三档里 `STRONG_SANDBOX` 在任何平台都不可达 (Seatbelt 没有独立
    PID namespace, bubblewrap 也未证明资源硬上限), 而 `PARTIAL` 与 `STRONG` 之间
    **没有任何行为差异的消费方** —— 全库唯一影响执行路径的读取用的就是 `contained`,
    那本来就是二元的.

    不存在"部分隔离": 一个说不清自己拦得住什么的围栏, 裁决时只能当作没有.
    """

    HOST_CONFINED = "host_confined"
    UNCONFINED = "unconfined"

    @property
    def contained(self) -> bool:
        return self is IsolationLevel.HOST_CONFINED


@dataclass(frozen=True)
class ExecutionProfile:
    """一次会话内稳定的执行环境事实."""

    platform: str
    isolation_level: IsolationLevel
    trusted_path: tuple[str, ...]
    shell_launch: ShellLaunch
    environment_inheritance: EnvironmentInheritance
    protected_roots_hash: str
    executable_resolution_version: str
    path_separator: str
    controlled_environment: tuple[tuple[str, str], ...] = ()
    path_normalization_version: str = PATH_NORMALIZATION_VERSION
    environment_sanitization_version: str = ENVIRONMENT_SANITIZATION_VERSION
    profile_version: str = PROFILE_VERSION

    def __post_init__(self) -> None:
        if not self.trusted_path:
            raise ValueError("ExecutionProfile.trusted_path 不能为空")
        if any(entry in (".", "") for entry in self.trusted_path):
            # PATH 里的 "." 会让"当前目录下有个同名文件"变成一次代码执行.
            raise ValueError("受控 PATH 不能包含当前目录")
        if any(not Path(entry).is_absolute() for entry in self.trusted_path):
            raise ValueError("受控 PATH 只能包含绝对目录")
        if self.path_separator not in (":", ";"):
            raise ValueError("ExecutionProfile.path_separator 非法")
        # 解析方言与真实启动程序的映射必须在画像构造期确定，不能执行时猜 POSIX。
        _ = self.shell_launch.dialect

    @property
    def execution_profile_hash(self) -> str:
        """授权信封绑定它. 任一字段变化 -> 旧授权失效, 请求重新裁决."""
        return digest(self)
