"""system prompt 的 section 按名缓存 (ADR-0056 决策 3).

会话段的每个 section 是一个 compute 函数. 缓存命中就不调它; 只在会话刷新点 (决策 7)
清一次. 要每次调用都重算的 section 必须用 `uncached_section` 并写明理由 —— 一个每轮变的
值悄悄进了前缀, 每一轮都在作废整个窗口, 而没有任何东西会报错.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["Section", "SectionCache", "session_section", "uncached_section"]

ComputeFn = Callable[[], str | None]


@dataclass(frozen=True)
class Section:
    name: str
    compute: ComputeFn
    cache_break: bool = False
    reason: str = ""


def session_section(name: str, compute: ComputeFn) -> Section:
    """算一次, 缓存到下一个刷新点."""
    return Section(name=name, compute=compute)


def uncached_section(name: str, compute: ComputeFn, reason: str) -> Section:
    """每次调用都重算. 值一变就打破前缀缓存, 所以必须给理由."""
    if not reason.strip():
        raise ValueError(f"uncached_section({name!r}) 必须写明理由")
    return Section(name=name, compute=compute, cache_break=True, reason=reason)


class SectionCache:
    """会话级. 一个 AgentTurnService 一份."""

    def __init__(self) -> None:
        self._values: dict[str, str | None] = {}

    def resolve(self, sections: tuple[Section, ...]) -> tuple[str | None, ...]:
        resolved: list[str | None] = []
        for section in sections:
            if not section.cache_break and section.name in self._values:
                resolved.append(self._values[section.name])
                continue
            value = section.compute()
            self._values[section.name] = value
            resolved.append(value)
        return tuple(resolved)

    def clear(self) -> None:
        self._values.clear()

    def sizes(self) -> dict[str, int]:
        """每个已算出的 section 有多少字符. 给 `context.assembled` 日志用."""
        return {
            name: len(value)
            for name, value in self._values.items()
            if value is not None
        }
