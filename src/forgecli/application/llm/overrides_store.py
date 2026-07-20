"""按用途模型覆盖的持久化端口 ModelOverridesStore（ADR-0011 §16）。

覆盖存放在项目级 forge.toml 的 `[model_overrides.<origin>]` 段（每个 origin 最多
一条 provider/model）。ConfigService 的扁平 SCHEMA 白名单面向标量偏好，不适合
嵌套表 + 删除操作，故独立端口（tomlkit 实现在 infrastructure）。

store 只做「段 <-> 原始 mapping」搬运，不校验 origin / provider / model——
校验归 ModelOverridesService（复用 build_model_overrides）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping


class ModelOverridesStore(ABC):
    @abstractmethod
    def load(self) -> dict[str, Mapping[str, str]]:
        """读取 [model_overrides] 段的原始 mapping；无文件 / 无段返回 {}。"""

    @abstractmethod
    def set_override(self, origin: str, provider: str, model: str) -> None:
        """写入 / 覆盖一个 origin 的 provider/model（round-trip 保留其余内容）。"""

    @abstractmethod
    def clear_override(self, origin: str) -> None:
        """删除一个 origin 的覆盖；不存在时为 no-op。"""
