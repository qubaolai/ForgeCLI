"""配置持久化端口（由 infrastructure 实现）。

ConfigStore 把“配置存放在哪里、用什么格式”隔离在 application 之外。
今天只有一个 JSON 文件来源（.forge/config.json）；将来要加用户级配置、环境变量、
远程配置或多来源合并时，只需新增 / 组合 ConfigStore 实现，application 不变。

约定：
    - load() 返回扁平化的 dotted-key -> 规范字符串；无配置文件时返回空 dict。
    - load() 解析失败时抛 ConfigReadError（已翻译成用户可理解的错误）。
    - save() 把给定覆盖项持久化；可重复调用且结果稳定（同输入同输出）。
    - store 不做配置键白名单 / 校验——那是 application 的封闭职责。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping


class ConfigStore(ABC):
    @abstractmethod
    def load(self) -> dict[str, str]:
        """读取并扁平化已持久化的配置；无文件返回 {}，解析失败抛 ConfigReadError。"""

    @abstractmethod
    def save(self, values: Mapping[str, str]) -> None:
        """持久化全部覆盖项（幂等、可复现）。"""

    @abstractmethod
    def remove(self, key: str) -> None:
        """删除一个 dotted-key 覆盖；键不存在时保持幂等。"""
