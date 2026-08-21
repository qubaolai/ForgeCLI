"""不信任进程环境变量的本机平台目录探测。"""

from __future__ import annotations

import os

__all__ = ["windows_known_directory", "windows_protected_directories"]

_CSIDL = {
    "program_files": 0x26,
    "program_data": 0x23,
    "profile": 0x28,
}


def windows_known_directory(kind: str) -> str | None:
    """通过 Windows API 取目录；非 Windows 或 API 失败时返回 None。"""
    if os.name != "nt":
        return None
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(32_768)
        windll = ctypes.windll  # type: ignore[attr-defined]
        if kind == "windows":
            length = windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer))
            return buffer.value if 0 < length < len(buffer) else None
        csidl = _CSIDL.get(kind)
        if csidl is None:
            return None
        result = windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer)
        return buffer.value if result == 0 and buffer.value else None
    except (AttributeError, OSError, ValueError):
        return None


def windows_protected_directories() -> tuple[str, ...]:
    """至少覆盖 Windows、Program Files 与 ProgramData 的真实 OS 位置。"""
    values = (
        windows_known_directory("windows"),
        windows_known_directory("program_files"),
        windows_known_directory("program_data"),
    )
    return tuple(dict.fromkeys(value for value in values if value))
