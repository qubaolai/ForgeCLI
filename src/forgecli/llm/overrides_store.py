"""按用途模型覆盖的持久化端口 ModelOverridesStore（ADR-0011 §16）。

覆盖存放在项目级 forge.json 的 ``model_overrides.<origin>` 段（每个 origin 最多
一条 provider/model）。ConfigService 的扁平 SCHEMA 白名单面向标量偏好，不适合
嵌套对象 + 删除操作，故独立端口（JSON 实现在 infrastructure）。

store 只做「段 <-> 原始 mapping」搬运，不校验 origin / provider / model——
校验归 ModelOverridesService。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path

from forgecli.common.json_io import read_document, write_document


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


# ---- 原 infrastructure/llm/overrides_json_store.py ----
# ModelOverridesStore 的 JSON 实现：项目 ``forge.json`` 的 ``model_overrides`` 段。
#
# 与 JsonConfigStore（扁平配置键）共享同一份 forge.json，但只操作
# ``model_overrides.<origin>``；读写经 infrastructure/json_io，其余内容原样保留
# （ADR-0011 §16：覆盖为项目级配置）。
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
