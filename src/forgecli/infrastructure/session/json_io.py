"""会话 JSON 读写的统一入口（对标 infrastructure/json_io）。

收敛保证：
    - 一致的落盘：先写临时文件再 rename，避免读到半截文件（state.json 用）。
    - 一致的错误：解析 / IO 失败统一翻成面向用户的 SessionStateError，不漏 traceback。
events.jsonl 是 append-only 追加，不经本模块的原子整文件写。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from forgecli.infrastructure.json_io import write_atomic
from forgecli.shared.errors import SessionStateError


def read_json(path: Path) -> dict[str, object] | None:
    """解析 JSON 对象；文件不存在返回 None；解析 / IO 失败抛 SessionStateError。"""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise SessionStateError(f"无法读取会话状态文件：{path}\n  原因：{exc}") from exc
    if not isinstance(data, dict):
        raise SessionStateError(f"会话状态文件格式不正确（应为对象）：{path}")
    return {str(key): value for key, value in data.items()}


def write_json_atomic(path: Path, data: Mapping[str, object]) -> None:
    """原子写出 JSON 对象（先临时文件再替换）。"""
    write_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
