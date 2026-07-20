"""ModelOverridesStore 的 tomlkit 实现：项目 forge.toml 的 [model_overrides] 段。

与 TomlConfigStore（扁平配置键）共享同一份 forge.toml，但只操作
`[model_overrides.<origin>]` 表；读写经 infrastructure/toml_io，round-trip
保留文件其余内容与注释（ADR-0011 §16：覆盖为项目级配置）。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import tomlkit

from forgecli.application.llm.overrides_store import ModelOverridesStore
from forgecli.infrastructure.toml_io import read_document, write_document

_SECTION = "model_overrides"


class TomlModelOverridesStore(ModelOverridesStore):
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, Mapping[str, str]]:
        section = read_document(self._path).get(_SECTION)
        if not isinstance(section, Mapping):
            return {}
        loaded: dict[str, Mapping[str, str]] = {}
        for origin, body in section.items():
            if isinstance(body, Mapping):
                loaded[str(origin)] = {
                    str(key): str(value) for key, value in body.items()
                }
        return loaded

    def set_override(self, origin: str, provider: str, model: str) -> None:
        doc = read_document(self._path)
        if _SECTION not in doc:
            doc[_SECTION] = tomlkit.table()
        section = doc[_SECTION]
        entry = tomlkit.inline_table()
        entry["provider"] = provider
        entry["model"] = model
        section[origin] = entry  # type: ignore[index]
        write_document(self._path, doc)

    def clear_override(self, origin: str) -> None:
        doc = read_document(self._path)
        section = doc.get(_SECTION)
        if not isinstance(section, Mapping) or origin not in section:
            return
        del section[origin]  # type: ignore[attr-defined]
        if len(section) == 0:
            del doc[_SECTION]
        write_document(self._path, doc)
