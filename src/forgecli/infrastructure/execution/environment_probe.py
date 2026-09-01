"""执行环境探测: 生成 ExecutionProfile 与净化后的环境快照.

只探测, 不改变宿主: 不装东西, 不写全局配置, 不联网. 探测到什么就如实报告什么.

`isolation_level` 在这里给的是**保守缺省** UNCONFINED. 真正的取值来自
`infrastructure/execution/sandbox/` 的行为自测 —— 那一步会真的去写一个边界外的文件并
确认失败. 缺省不是"探测不出来先当有围栏用".

受控 PATH 由**启动 shell 的 PATH 减去 Agent 可写的部分**得到, 不再是一份写死的候选
目录清单. 减的只有两类: 相对路径条目, 以及落在工作区里的目录. 判据与理由见
`domain/execution/environment` 的模块说明.

拿不到继承 PATH 时不再兜底猜一份系统目录: 猜出来的那份既不等于开发者的环境, 又让
"为什么这个工具找不到"变成一个查不出来的问题. 空 PATH 会让 ExecutionProfile 直接拒绝
构造, 那是一个响亮的失败, 好过一个安静的错误答案.
"""

from __future__ import annotations

import os
import platform
from collections.abc import Mapping, Sequence
from pathlib import Path

from forgecli.application.security.executable_resolver import (
    EXECUTABLE_RESOLUTION_VERSION,
)
from forgecli.domain.execution.environment import (
    EnvironmentInheritance,
    ShellLaunch,
    sanitize_environment,
)
from forgecli.domain.execution.profile import ExecutionProfile, IsolationLevel
from forgecli.infrastructure.execution.sandbox.wsl2 import wsl2_bubblewrap_available
from forgecli.infrastructure.platform_paths import windows_known_directory

__all__ = ["build_execution_environment", "probe_execution_profile"]


def probe_execution_profile(
    *,
    protected_roots_hash: str,
    workspace_roots: Sequence[str] = (),
    inherited_path: str | None = None,
    inheritance: EnvironmentInheritance = EnvironmentInheritance.ALL,
) -> ExecutionProfile:
    """按当前平台与启动 shell 生成执行画像.

    `workspace_roots` 是唯一的减项来源: 工作区是所有隔离档下都确定属于 Agent 可写的
    地方. 其余目录不减 —— 有围栏时 Agent 写不进去, 没围栏时每条 shell 命令都要人点头,
    而且可执行文件身份那一闸还会拒绝让 Agent 可写位置的文件继承同名系统工具的授权
    (`ExecutableIdentity.eligible_for_plain_allow`). 在这里再筛一遍是重复.
    """
    is_windows = os.name == "nt"
    separator = os.pathsep
    raw_path = os.environ.get("PATH", "") if inherited_path is None else inherited_path
    trusted_path = filter_inherited_path(
        raw_path, workspace_roots=workspace_roots, path_separator=separator
    )
    return ExecutionProfile(
        platform=f"{platform.system()}-{platform.machine()}",
        isolation_level=IsolationLevel.UNCONFINED,
        trusted_path=trusted_path,
        shell_launch=_shell_launch(is_windows),
        environment_inheritance=inheritance,
        controlled_environment=_controlled_environment(is_windows),
        protected_roots_hash=protected_roots_hash,
        executable_resolution_version=EXECUTABLE_RESOLUTION_VERSION,
        path_separator=separator,
    )


def filter_inherited_path(
    raw_path: str,
    *,
    workspace_roots: Sequence[str] = (),
    path_separator: str = ":",
) -> tuple[str, ...]:
    """继承的 PATH 减去 Agent 可写的部分, 顺序与去重都保持继承时的样子.

    顺序要保住: 开发者把 temurin-11 排在 `/usr/bin` 前面是有意的, 重排等于换了一个
    java. 这正是"等价性"要的东西.

    丢掉的两类:

    - 相对路径 (含 `.` 与空串). PATH 里的 `.` 会让"当前目录下有个同名文件"变成一次
      代码执行, 而 Agent 恰好一直在往当前目录写文件.
    - 工作区内的目录. `node_modules/.bin` 与 `.venv/bin` 都落在这里.
    """
    roots = tuple(str(Path(root).expanduser()) for root in workspace_roots if root)
    kept: list[str] = []
    for entry in raw_path.split(path_separator):
        if not entry or not Path(entry).is_absolute():
            continue
        resolved = str(Path(entry).expanduser())
        if any(_is_within(resolved, root) for root in roots):
            continue
        kept.append(resolved)
    return tuple(dict.fromkeys(kept))


def _is_within(path: str, root: str) -> bool:
    try:
        Path(path).relative_to(Path(root))
    except ValueError:
        return False
    return True


def build_execution_environment(
    profile: ExecutionProfile, raw: Mapping[str, str] | None = None
) -> dict[str, str]:
    """按画像净化环境. 默认基于 os.environ, 测试可以传入固定映射."""
    return sanitize_environment(
        dict(os.environ if raw is None else raw),
        trusted_path=profile.trusted_path,
        inheritance=profile.environment_inheritance,
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


def _shell_launch(is_windows: bool) -> ShellLaunch:
    """非交互, 非登录, 不加载 profile/rc 的启动方式 (ADR-0014 §4.2)."""
    if is_windows:
        system_root = windows_known_directory("windows") or r"C:\Windows"
        if wsl2_bubblewrap_available():
            # 有 WSL2 就走它: 那是 Windows 上唯一能拿到真围栏的路径 (ADR-0030 决策 5).
            #
            # 这也意味着命令跑在 **Linux** 里 —— `python` / `node` / `git` 是 WSL
            # 那一套, `.exe` 与 PowerShell 脚本在这条路径上跑不了. 这个取舍是明确的:
            # 用一个真围栏换掉原生 Windows shell, 而不是留着原生 shell 靠静态分析
            # 假装安全.
            return ShellLaunch(
                program=str(Path(system_root) / "System32" / "wsl.exe"),
                args=("-e", "/bin/bash", "--noprofile", "--norc", "-c"),
                kind="bash",
            )
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
