"""人工 Shell 的平台实现 (ADR-0017 §8).

按 sys.platform 分派. 两个 Provider 的共同选择是**不接管字节流** —— Forge 的终端本来
就是用户的终端, 直接交给子 Shell 比转发一遍更原生, 而 §9 又禁止观察那些字节.
"""

from __future__ import annotations

import sys

from forgecli.application.manual_shell.provider import InteractiveShellProvider
from forgecli.infrastructure.manual_shell.shell_selection import SystemShellResolver

__all__ = ["SystemShellResolver", "build_interactive_shell_provider"]


def build_interactive_shell_provider() -> InteractiveShellProvider:
    if sys.platform == "win32":
        from forgecli.infrastructure.manual_shell.windows_console import (
            WindowsInteractiveShellProvider,
        )

        return WindowsInteractiveShellProvider()
    from forgecli.infrastructure.manual_shell.posix_pty import (
        PosixInteractiveShellProvider,
    )

    return PosixInteractiveShellProvider()
