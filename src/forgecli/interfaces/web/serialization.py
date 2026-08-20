"""Web DTO 的安全 JSON 转换。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any


def to_jsonable(value: object) -> Any:
    """只展开值对象、枚举和基础容器，不调用任意对象的 ``__dict__``。"""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: to_jsonable(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list | set | frozenset):
        return [to_jsonable(item) for item in value]
    raise TypeError(f"不支持的 Web DTO 类型: {type(value).__name__}")
