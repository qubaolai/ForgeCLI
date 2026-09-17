"""planning 工具的内部函数: 把 schema 校验过的入参收窄回具体类型."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

__all__ = ["index_of", "mappings_of", "strings_of"]


def strings_of(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    items: list[str] = []
    for item in value:
        items.append(str(item))
    return tuple(items)


def index_of(value: object) -> int:
    """schema 保证它是整数; 这里只是把类型收窄, 非整数一律给 -1 让不变量去报错."""
    if isinstance(value, int):
        return value
    return -1


def mappings_of(value: object) -> tuple[Mapping[str, object], ...]:
    """schema 已经校验过形状, 这里只是把类型收窄回来."""
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    items: list[Mapping[str, object]] = []
    for item in value:
        if isinstance(item, Mapping):
            items.append(item)
    return tuple(items)
