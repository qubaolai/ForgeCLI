"""选哪个 Shell 启动 (ADR-0017 §6.1).

选择顺序有意从"用户自己配的"开始, 而不是从"我们知道一定存在的"开始: 人工 Shell 的
全部意义就是让用户拿回熟悉的环境, 挑一个他没在用的 Shell 等于把这个功能做废了一半.

POSIX: `$SHELL` (绝对路径且可执行) -> 执行画像探测到的 Shell -> `/bin/sh`.
Windows: `pwsh` -> `powershell` -> `%COMSPEC%` -> `cmd.exe`.

启动参数默认 **interactive non-login** (`-i`): login shell 会再跑一遍 `.zprofile`
`.bash_profile`, 而 Forge 本身就是从一个已经 login 过的 shell 里起来的, 重复执行 login
profile 会让 PATH 之类的东西被追加两次.

这里解析出来的路径**不进** Agent 的 executable allowlist, 也不经过
ToolAuthorizationService (§6.1) —— 用户自己的 Shell 不需要向 Forge 证明身份.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from forgecli.application.manual_shell.provider import (
    InteractiveShellResolver,
    ManualShellContext,
    ManualShellUnavailable,
)
from forgecli.domain.execution.profile import ExecutionProfile
from forgecli.domain.manual_shell.request import ManualShellRequest

__all__ = ["SystemShellResolver"]

# 走 -i 的 Shell. 其余 (fish, csh…) 也认 -i, 但只对确认过的给参数, 认不出的裸启动.
_INTERACTIVE_FLAG = frozenset({"sh", "bash", "zsh", "ksh", "dash", "fish"})
_POSIX_FALLBACK = "/bin/sh"
_WINDOWS_CANDIDATES = ("pwsh.exe", "powershell.exe", "cmd.exe")


class SystemShellResolver(InteractiveShellResolver):
    def __init__(self, profile: ExecutionProfile) -> None:
        self._profile = profile

    def resolve(self, context: ManualShellContext) -> ManualShellRequest:
        executable = (
            self._windows(context.environment)
            if os.name == "nt"
            else self._posix(context.environment)
        )
        if executable is None:
            raise ManualShellUnavailable(
                "找不到可用的交互式 Shell. "
                "请设置 $SHELL 指向一个存在且可执行的 Shell (Windows 上设置 %COMSPEC%)."
            )
        return ManualShellRequest(
            shell_executable=executable,
            argv=(executable, *_arguments(executable, context.command)),
            cwd=context.cwd,
            environment=_marked(context.environment, context.cwd),
            terminal=context.terminal,
            command=context.command,
        )

    def _posix(self, environment: Mapping[str, str]) -> str | None:
        configured = environment.get("SHELL", "").strip()
        # 只认绝对路径: 相对的 "SHELL=zsh" 要靠 PATH 查找, 而人工 Shell 用的是用户环境
        # 快照里的 PATH —— 那个 PATH 是 Agent 可写目录也可能在里面的那份.
        if configured.startswith("/") and _executable(configured):
            return configured
        probed = self._profile.shell_launch.program
        if probed and _executable(probed):
            return probed
        return _POSIX_FALLBACK if _executable(_POSIX_FALLBACK) else None

    def _windows(self, environment: Mapping[str, str]) -> str | None:
        comspec = environment.get("COMSPEC", "").strip()
        for candidate in (*_WINDOWS_CANDIDATES[:2], comspec, _WINDOWS_CANDIDATES[2]):
            if not candidate:
                continue
            found = candidate if _executable(candidate) else _on_path(candidate)
            if found:
                return found
        return None


def _arguments(executable: str, command: str) -> tuple[str, ...]:
    """启动参数. 一次性命令与完整会话走同一个 Shell, 只差最后那段 `-c`.

    一次性命令**仍然带 `-i`**: 那是 `# ll` 里的 alias, 函数和 rc 里配的 PATH 能生效的
    唯一办法. 代价是每次都要 source 一遍 rc —— 但用户抱怨的"太重"是那套进入退出的
    仪式, 不是几十毫秒, 而一个不认识自己 alias 的 shell 才真的不像自己的 shell.
    """
    name = Path(executable).name.lower().removesuffix(".exe")
    if name in _INTERACTIVE_FLAG:
        return ("-i", "-c", command) if command else ("-i",)
    if name in ("pwsh", "powershell"):
        # PowerShell 默认就是交互式, 加 -i 是非法参数.
        return ("-Command", command) if command else ()
    if name == "cmd":
        return ("/c", command) if command else ()
    return ("-c", command) if command else ()


def _marked(environment: Mapping[str, str], cwd: str) -> Mapping[str, str]:
    """打上"你正在 Forge 里"的标记.

    用环境变量而不是改 prompt, 因为 ADR-0017 §4 要求"Forge 不伪造 Shell prompt" ——
    往别人的 PROMPT 里插东西会打坏 powerlevel10k / starship 这类自己拼提示符的配置,
    而且各 shell 的注入点都不一样.

    环境变量是这类"我在某个子环境里"的通行做法 (VIRTUAL_ENV, IN_NIX_SHELL, TMUX, STY):
    Forge 只负责把事实摆出来, 怎么显示由用户自己在 prompt 里决定. 进入提示会告诉他
    这个变量存在.

    FORGE_SHELL 还有第二个用处: 嵌套启动 forge 时能据此认出来, 项目锁的报错可以说得
    更准 (§11 不为递归 forge 加特例, 但至少能说清是怎么回事).
    """
    marked = dict(environment)
    marked["FORGE_SHELL"] = "1"
    marked["FORGE_SHELL_CWD"] = cwd
    return MappingProxyType(marked)


def _executable(path: str) -> bool:
    try:
        return os.path.isfile(path) and os.access(path, os.X_OK)
    except OSError:
        return False


def _on_path(name: str) -> str | None:
    from shutil import which

    return which(name)
