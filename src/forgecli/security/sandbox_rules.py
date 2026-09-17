"""哪些 Bash 命令进围栏 (对应 tools/BashTool/shouldUseSandbox.ts).

放在 security 而不是 Bash 工具目录里, 因为裁决顺序的 1b 步 (整工具 ask 规则的沙箱
例外) 也要问这个问题, 而 security 不 import tools.
"""

from __future__ import annotations

from collections.abc import Mapping

from forgecli.core.sandbox import SandboxSettings

__all__ = ["contains_excluded_command", "should_use_sandbox"]


def contains_excluded_command(command: str, excluded: tuple[str, ...]) -> bool:
    """`excludedCommands` 里的条目按前缀比, 末尾的 `*` 表示任意后缀."""
    stripped = command.strip()
    for pattern in excluded:
        entry = pattern.strip()
        if not entry:
            continue
        if entry.endswith("*"):
            if stripped.startswith(entry[:-1].rstrip()):
                return True
            continue
        if stripped == entry or stripped.startswith(entry + " "):
            return True
    return False


def should_use_sandbox(input: Mapping[str, object], sandbox: SandboxSettings) -> bool:
    if not sandbox.active:
        return False
    disable = input.get("dangerously_disable_sandbox")
    if disable is True and sandbox.allow_unsandboxed_commands:
        return False
    command = input.get("command")
    if not isinstance(command, str) or not command:
        return False
    return not contains_excluded_command(command, sandbox.excluded_commands)
