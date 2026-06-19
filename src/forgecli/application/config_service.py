"""配置读写服务(端口 + 临时实现)。

真实实现应持久化到配置文件，届时迁到 infrastructure/config/
本文件只保留 ConfigService 端口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class ConfigService(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...


@dataclass
class InMemoryConfigService:
    """占位实现：仅存内存。真实实现持久化到 ~/.config/forge 之类位置。"""

    _values: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str | None:
        return self._values.get(key)

    def set(self, key: str, value: str) -> None:
        self._values[key] = value