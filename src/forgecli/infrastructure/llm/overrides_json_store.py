"""ModelOverridesStore 的 JSON 实现：项目 ``forge.json`` 的 ``model_overrides`` 段。

与 JsonConfigStore（扁平配置键）共享同一份 forge.json，但只操作
``model_overrides.<origin>``；读写经 infrastructure/json_io，其余内容原样保留
（ADR-0011 §16：覆盖为项目级配置）。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from forgecli.application.llm.overrides_store import ModelOverridesStore
from forgecli.infrastructure.json_io import read_document, write_document

_SECTION = "model_overrides"


class JsonModelOverridesStore(ModelOverridesStore):
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, Mapping[str, str]]:
        section = read_document(self._path).get(_SECTION)
        if not isinstance(section, Mapping):
            return {}
        return {
            str(origin): {str(key): str(value) for key, value in body.items()}
            for origin, body in section.items()
            if isinstance(body, Mapping)
        }

    def set_override(self, origin: str, provider: str, model: str) -> None:
        document = read_document(self._path)
        section = document.get(_SECTION)
        if not isinstance(section, dict):
            section = {}
            document[_SECTION] = section
        section[origin] = {"provider": provider, "model": model}
        write_document(self._path, document)

    def clear_override(self, origin: str) -> None:
        document = read_document(self._path)
        section = document.get(_SECTION)
        if not isinstance(section, dict) or origin not in section:
            return
        del section[origin]
        if not section:
            del document[_SECTION]
        write_document(self._path, document)
