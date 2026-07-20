"""LlmConfigStore 的 tomlkit 实现：.forge/llm.toml。

llm.toml 是「人机共编」的文件——用户手写自定义模型与厂商 JSON 超参，菜单又要能改单个值。
读写都经 infrastructure/toml_io（统一走 tomlkit，round-trip 保留注释与既有结构）。

行为对齐 application/llm/config/ports.LlmConfigStore：
    - load(): 返回 [llm] 段的 plain dict；无文件返回 {}；解析失败抛 ConfigReadError。
    - upsert/remove: 解析既有文档 -> 局部修改 -> 原子写回，保留其余内容。
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import cast

import tomlkit
from tomlkit.items import InlineTable, Table

from forgecli.application.llm.config.llm_config_store import LlmConfigStore
from forgecli.infrastructure.toml_io import read_document, write_document

_SECTION = "llm"


class TomlLlmConfigStore(LlmConfigStore):
    def __init__(self, path: Path) -> None:
        self._path = path

    # ---- 读 ----

    def load(self) -> dict[str, object]:
        doc = read_document(self._path)
        section = doc.get(_SECTION)
        if not isinstance(section, Mapping):
            return {}
        unwrap = getattr(section, "unwrap", None)
        unwrapped = unwrap() if callable(unwrap) else dict(section)
        if not isinstance(unwrapped, Mapping):
            return {}
        return dict(cast(Mapping[str, object], unwrapped))

    # ---- 写（round-trip）----

    def upsert_model(
        self,
        provider_id: str,
        model_id: str,
        fields: Mapping[str, object],
        *,
        provider_defaults: Mapping[str, object],
    ) -> None:
        doc = read_document(self._path)
        models_table = self._provider_models_table(doc, provider_id, provider_defaults)
        models_table[model_id] = self._inline(fields)
        write_document(self._path, doc)

    def remove_model(self, provider_id: str, model_id: str) -> None:
        doc = read_document(self._path)
        providers = doc.get(_SECTION, {}).get("providers", {})
        provider = (
            providers.get(provider_id) if isinstance(providers, Mapping) else None
        )
        models = provider.get("models") if isinstance(provider, Mapping) else None
        if isinstance(models, MutableMapping) and model_id in models:
            del models[model_id]
            write_document(self._path, doc)

    def upsert_provider_field(
        self,
        provider_id: str,
        field: str,
        value: object,
        *,
        provider_defaults: Mapping[str, object],
    ) -> None:
        doc = read_document(self._path)
        provider = self._ensure_provider(doc, provider_id, provider_defaults)
        provider[field] = value
        write_document(self._path, doc)

    def upsert_runtime_field(self, section: str, field: str, value: object) -> None:
        """写入应用级 [llm.<section>] 配置，保留其他段与注释。"""
        doc = read_document(self._path)
        llm = doc.setdefault(_SECTION, tomlkit.table())
        runtime = llm.setdefault(section, tomlkit.table())
        runtime[field] = value
        write_document(self._path, doc)

    # ---- 内部 ----

    def _ensure_provider(
        self,
        doc: tomlkit.TOMLDocument,
        provider_id: str,
        provider_defaults: Mapping[str, object],
    ) -> Table:
        section = doc.setdefault(_SECTION, tomlkit.table())
        providers = section.setdefault("providers", tomlkit.table())
        if provider_id not in providers:
            provider = tomlkit.table()
            for key, value in provider_defaults.items():
                if value not in (None, ""):
                    provider[key] = value
            provider["models"] = tomlkit.table()
            providers[provider_id] = provider
        return cast(Table, providers[provider_id])

    def _provider_models_table(
        self,
        doc: tomlkit.TOMLDocument,
        provider_id: str,
        provider_defaults: Mapping[str, object],
    ) -> Table:
        provider = self._ensure_provider(doc, provider_id, provider_defaults)
        if "models" not in provider:
            provider["models"] = tomlkit.table()
        return cast(Table, provider["models"])

    @staticmethod
    def _inline(fields: Mapping[str, object]) -> InlineTable:
        inline = tomlkit.inline_table()
        for key, value in fields.items():
            inline[key] = value
        return inline
