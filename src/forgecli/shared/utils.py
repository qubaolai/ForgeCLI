"""全局公共工具函数
"""

from __future__ import annotations

from datetime import datetime

def now_iso() -> str:
    """本地时区、秒级 ISO 时间戳，如 ``2026-06-27T10:30:00+08:00``。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")