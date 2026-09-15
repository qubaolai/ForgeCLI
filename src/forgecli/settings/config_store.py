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
from pathlib import Path
from typing import Any

from forgecli.common.json_io import read_document, write_document


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


# ---- 原 infrastructure/config/json_store.py ----
# ConfigStore 的 JSON 实现：``.forge/config.json`` 与项目 ``forge.json``。
#
# 行为对齐 application/config/config_store.ConfigStore：
#     - load(): 把文档扁平化成 dotted-key -> 规范字符串；无文件返回 {}；
#       嵌套对象 {"output": {"theme": ...}} 与扁平键 "output.theme" 都归一成
#       "output.theme"，值统一为字符串（裸 bool -> "true"/"false"）。
#     - save(): 把覆盖项逐个写入文档（保留其余内容），原子落盘；可重复。
#     - 解析失败抛 ConfigReadError（在 json_io 内翻译，不漏 traceback）。
def _stringify(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _flatten(data: Mapping[str, object], prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in data.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.update(_flatten(value, prefix=f"{dotted}."))
        else:
            flat[dotted] = _stringify(value)
    return flat


class JsonConfigStore(ConfigStore):
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, str]:
        return _flatten(read_document(self._path))

    def save(self, values: Mapping[str, str]) -> None:
        document = read_document(self._path)
        for dotted, value in values.items():
            _set(document, dotted.split("."), value)
        write_document(self._path, document)

    def remove(self, key: str) -> None:
        document = read_document(self._path)
        if _remove(document, key.split(".")):
            write_document(self._path, document)


def _set(document: dict[str, Any], path: list[str], value: str) -> None:
    """按 dotted key 逐段下钻；中途遇到非对象就地换成对象，让写入总能落下去。"""
    table = document
    for segment in path[:-1]:
        nested = table.get(segment)
        if not isinstance(nested, dict):
            nested = {}
            table[segment] = nested
        table = nested
    table[path[-1]] = value


def _remove(document: dict[str, Any], path: list[str]) -> bool:
    """删一个叶子并清掉由此变空的父对象；未知键不改写文件。"""
    if not path:
        return False
    head, *tail = path
    if not tail:
        if head not in document:
            return False
        del document[head]
        return True
    nested = document.get(head)
    if not isinstance(nested, dict) or not _remove(nested, tail):
        return False
    if not nested:
        document.pop(head, None)
    return True
