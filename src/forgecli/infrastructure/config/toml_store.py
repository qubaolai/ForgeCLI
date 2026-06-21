"""ConfigStore 的 tomlkit 实现：.forge/config.toml。

读写都经 infrastructure/toml_io（统一走 tomlkit，round-trip 保留注释）。

行为对齐 application/config/ports.ConfigStore：
    - load(): 把文档扁平化成 dotted-key -> 规范字符串；无文件返回 {}；
      嵌套表 [output] theme=... 与 dotted output.theme=... 都归一成 "output.theme"，
      值统一为字符串（裸 bool -> "true"/"false"）。
    - save(): 把覆盖项逐个写入文档（保留其余内容与注释），原子落盘；可重复。
    - 解析失败抛 ConfigReadError（在 toml_io 内翻译，不漏 traceback）。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import tomlkit

from forgecli.application.config.ports import ConfigStore
from forgecli.infrastructure.toml_io import read_document, write_document


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


class TomlConfigStore(ConfigStore):
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, str]:
        doc = read_document(self._path)
        return _flatten(doc.unwrap())

    def save(self, values: Mapping[str, str]) -> None:
        doc = read_document(self._path)
        for dotted, value in values.items():
            self._set(doc, dotted.split("."), value)
        write_document(self._path, doc)

    @staticmethod
    def _set(doc: tomlkit.TOMLDocument, path: list[str], value: str) -> None:
        table: object = doc
        for segment in path[:-1]:
            if segment not in table:  # type: ignore[operator]
                table[segment] = tomlkit.table()  # type: ignore[index]
            table = table[segment]  # type: ignore[index]
        table[path[-1]] = value  # type: ignore[index]
