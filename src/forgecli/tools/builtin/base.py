"""内置工具共用的小函数: 取入参, 展开路径, 拼结果块."""

from __future__ import annotations

import os
from collections.abc import Mapping

from forgecli.core.message import ToolResultBlock

__all__ = [
    "expand_input_path",
    "int_of",
    "str_of",
    "text_block",
]


def str_of(input: Mapping[str, object], key: str, default: str = "") -> str:
    value = input.get(key)
    if isinstance(value, str):
        return value
    return default


def int_of(input: Mapping[str, object], key: str) -> int | None:
    value = input.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def expand_input_path(path: str, cwd: str) -> str:
    """`~` 展开, 相对路径按 cwd 补齐, 归一化. 与 security/filesystem.expand_path 同义,
    工具这边不 import 那个模块以免把权限逻辑拖进来."""
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        expanded = os.path.join(cwd, expanded)
    return os.path.normpath(expanded)


def text_block(
    tool_use_id: str, text: str, *, is_error: bool = False
) -> ToolResultBlock:
    return ToolResultBlock(tool_call_id=tool_use_id, content=text, is_error=is_error)
