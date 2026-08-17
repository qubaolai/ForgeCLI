"""执行环境探测: 生成 ExecutionProfile 与净化后的环境快照.

只探测, 不改变宿主: 不装东西, 不写全局配置, 不联网. 探测到什么就如实报告什么 —— 隔离
等级当前恒为 NO_SANDBOX, 因为本次没有实现沙箱层, 而不是"探测不出来先当强隔离用".

受控 PATH 的构造原则: 只收标准系统目录与用户显式配置的工具链目录. 不含 `.`, 不含工作
区, 不含临时目录, 不含 node_modules/.bin —— 那些目录 Agent 自己能写.
"""

from __future__ import annotations

import os
import platform
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from forgecli.domain.execution.environment import (
    DEFAULT_ENV_ALLOWLIST,
    ShellLaunch,
    sanitize_environment,
)
from forgecli.domain.execution.profile import ExecutionProfile, IsolationLevel

__all__ = ["build_execution_environment", "probe_execution_profile"]

_POSIX_PATH_CANDIDATES: tuple[str, ...] = (
    "/usr/local/bin",
    "/opt/homebrew/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
)


def probe_execution_profile(
    *,
    protected_roots_hash: str,
    toolchain_dirs: Sequence[str] = (),
) -> ExecutionProfile:
    """按当前平台生成执行画像.

    toolchain_dirs 是用户**显式配置**的工具链目录 (例如项目虚拟环境). 它们会进 PATH,
    但 ADR-0014 §4.2 要求把这类目录标记为可写/不可信 —— 标记发生在可执行文件身份解析
    那一步 (阶段 4), 这里只负责如实把它们放进画像, 让 profile hash 能反映这个选择.
    """
    is_windows = os.name == "nt"
    entries = [entry for entry in toolchain_dirs if entry]
    entries.extend(_platform_path_entries(is_windows))
    trusted_path = tuple(dict.fromkeys(entry for entry in entries if entry != "."))
    return ExecutionProfile(
        platform=f"{platform.system()}-{platform.machine()}",
        isolation_level=IsolationLevel.NO_SANDBOX,
        trusted_path=trusted_path or (str(Path(sys.executable).parent),),
        shell_launch=_shell_launch(is_windows),
        environment_allowlist=DEFAULT_ENV_ALLOWLIST,
        protected_roots_hash=protected_roots_hash,
    )


def build_execution_environment(
    profile: ExecutionProfile, raw: Mapping[str, str] | None = None
) -> dict[str, str]:
    """按画像净化环境. 默认基于 os.environ, 测试可以传入固定映射."""
    return sanitize_environment(
        dict(os.environ if raw is None else raw),
        trusted_path=profile.trusted_path,
        allowlist=profile.environment_allowlist,
    )


def _platform_path_entries(is_windows: bool) -> list[str]:
    if is_windows:
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        return [
            str(Path(system_root) / "System32"),
            system_root,
            str(Path(system_root) / "System32" / "Wbem"),
        ]
    return [entry for entry in _POSIX_PATH_CANDIDATES if Path(entry).is_dir()]


def _shell_launch(is_windows: bool) -> ShellLaunch:
    """非交互, 非登录, 不加载 profile/rc 的启动方式 (ADR-0014 §4.2)."""
    if is_windows:
        return ShellLaunch(
            program=str(
                Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
                / "System32"
                / "cmd.exe"
            ),
            args=("/D", "/S", "/C"),
            kind="cmd",
        )
    bash = next(
        (path for path in ("/bin/bash", "/usr/bin/bash") if Path(path).exists()), None
    )
    if bash is not None:
        return ShellLaunch(
            program=bash, args=("--noprofile", "--norc", "-c"), kind="bash"
        )
    return ShellLaunch(program="/bin/sh", args=("-c",), kind="sh")
