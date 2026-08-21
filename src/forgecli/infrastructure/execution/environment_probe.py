"""执行环境探测: 生成 ExecutionProfile 与净化后的环境快照.

只探测, 不改变宿主: 不装东西, 不写全局配置, 不联网. 探测到什么就如实报告什么.

`isolation_level` 在这里给的是**保守缺省** UNCONFINED. 真正的取值来自
`infrastructure/execution/sandbox/` 的行为自测 —— 那一步会真的去写一个边界外的文件并
确认失败. 缺省不是"探测不出来先当有围栏用".

受控 PATH 的构造原则: 只收标准系统目录与用户显式配置的工具链目录. 不含 `.`, 不含工作
区, 不含临时目录, 不含 node_modules/.bin —— 那些目录 Agent 自己能写.
"""

from __future__ import annotations

import os
import platform
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from forgecli.application.security.executable_resolver import (
    EXECUTABLE_RESOLUTION_VERSION,
)
from forgecli.domain.execution.environment import (
    DEFAULT_ENV_ALLOWLIST,
    ShellLaunch,
    sanitize_environment,
)
from forgecli.domain.execution.profile import ExecutionProfile, IsolationLevel
from forgecli.infrastructure.platform_paths import windows_known_directory

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
    toolchains = tuple(
        dict.fromkeys(
            str(Path(entry).expanduser().resolve()) for entry in toolchain_dirs if entry
        )
    )
    entries = list(toolchains)
    entries.extend(_platform_path_entries(is_windows))
    trusted_path = tuple(dict.fromkeys(entry for entry in entries if entry != "."))
    return ExecutionProfile(
        platform=f"{platform.system()}-{platform.machine()}",
        isolation_level=IsolationLevel.UNCONFINED,
        trusted_path=trusted_path or (str(Path(sys.executable).parent),),
        writable_toolchain_path=toolchains,
        shell_launch=_shell_launch(is_windows),
        environment_allowlist=DEFAULT_ENV_ALLOWLIST,
        controlled_environment=_controlled_environment(is_windows),
        protected_roots_hash=protected_roots_hash,
        executable_resolution_version=EXECUTABLE_RESOLUTION_VERSION,
        path_separator=os.pathsep,
    )


def build_execution_environment(
    profile: ExecutionProfile, raw: Mapping[str, str] | None = None
) -> dict[str, str]:
    """按画像净化环境. 默认基于 os.environ, 测试可以传入固定映射."""
    return sanitize_environment(
        dict(os.environ if raw is None else raw),
        trusted_path=profile.trusted_path,
        allowlist=profile.environment_allowlist,
        controlled=dict(profile.controlled_environment),
        path_separator=profile.path_separator,
    )


def _controlled_environment(is_windows: bool) -> tuple[tuple[str, str], ...]:
    """关闭会改变命令语义的隐式用户配置；值随 profile 一起绑定授权。"""
    null = "NUL" if is_windows else "/dev/null"
    home = _account_home(is_windows)
    temporary = _system_temp(is_windows)
    values = [
        ("GIT_CONFIG_NOSYSTEM", "1"),
        ("GIT_CONFIG_GLOBAL", null),
        ("PYTHONNOUSERSITE", "1"),
        ("PIP_CONFIG_FILE", null),
        ("NPM_CONFIG_USERCONFIG", null),
        ("CURL_HOME", null),
        ("WGETRC", null),
        ("HOME", home),
        ("TMPDIR", temporary),
        ("TMP", temporary),
        ("TEMP", temporary),
    ]
    if is_windows:
        values.append(("USERPROFILE", home))
    return tuple(values)


def _account_home(is_windows: bool) -> str:
    """从账户数据库取 HOME，避免可修改的 HOME 环境变量重定向工具配置。"""
    if is_windows:
        known = windows_known_directory("profile")
        if known:
            return known
    if not is_windows:
        try:
            import pwd

            return pwd.getpwuid(os.getuid()).pw_dir
        except (ImportError, KeyError, OSError):
            pass
    return str(Path.home().resolve())


def _system_temp(is_windows: bool) -> str:
    if is_windows:
        root = windows_known_directory("windows") or r"C:\Windows"
        return str(Path(root) / "Temp")
    return "/tmp"


def _platform_path_entries(is_windows: bool) -> list[str]:
    if is_windows:
        system_root = windows_known_directory("windows") or r"C:\Windows"
        return [
            str(Path(system_root) / "System32"),
            system_root,
            str(Path(system_root) / "System32" / "Wbem"),
        ]
    return [entry for entry in _POSIX_PATH_CANDIDATES if Path(entry).is_dir()]


def _shell_launch(is_windows: bool) -> ShellLaunch:
    """非交互, 非登录, 不加载 profile/rc 的启动方式 (ADR-0014 §4.2)."""
    if is_windows:
        system_root = windows_known_directory("windows") or r"C:\Windows"
        return ShellLaunch(
            program=str(Path(system_root) / "System32" / "cmd.exe"),
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
