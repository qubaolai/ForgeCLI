"""LLM 配置持久化端口（由 infrastructure 用 JSON 实现）。

约定：
    - load() 返回 [llm] 段落的原始嵌套 dict（plain python），无文件返回 {}。
    - load() 解析失败抛 ConfigReadError（已翻成用户可理解的错误）。
    - upsert_model() / remove_model() / upsert_provider_field() /
      upsert_runtime_field() 做 round-trip 写入：
      保留文件里其余内容与注释。
    - store 不做供应商白名单 / 参数校验——那是 application 的封闭职责。
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any

from forgecli.common.json_io import read_document, write_document

# ---- 原 infrastructure/llm/json_store.py ----
# JsonLlmConfigStore 的 JSON 实现：``.forge/llm.json``。
#
# llm.json 是「人机共编」的文件——用户手写自定义模型与厂商超参，菜单又要能改单个值。
# 读写都经 infrastructure/json_io（局部修改后整份原子写回，保留其余内容）。
#
# 行为对齐 application/llm/config/llm_config_store.JsonLlmConfigStore：
#     - load(): 返回 ``llm`` 段的 plain dict; 无文件返回 {}; 解析失败抛
#       ConfigReadError.
#     - upsert/remove: 读出文档 -> 局部修改 -> 原子写回。
_SECTION = "llm"


class JsonLlmConfigStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    # ---- 读 ----

    def load(self) -> dict[str, object]:
        """读取 [llm] 段为嵌套 dict；无文件返回 {}，解析失败抛 ConfigReadError。"""
        section = read_document(self._path).get(_SECTION)
        return dict(section) if isinstance(section, Mapping) else {}

    # ---- 写 ----

    def upsert_model(
        self,
        provider_id: str,
        model_id: str,
        fields: Mapping[str, object],
        *,
        provider_defaults: Mapping[str, object],
    ) -> None:
        """新增 / 覆盖一个模型；provider 段不存在则用默认建立（round-trip）。"""
        document = read_document(self._path)
        provider = _ensure_provider(document, provider_id, provider_defaults)
        models = provider.setdefault("models", {})
        if not isinstance(models, dict):
            models = {}
            provider["models"] = models
        models[model_id] = dict(fields)
        write_document(self._path, document)

    def remove_model(self, provider_id: str, model_id: str) -> None:
        """删除一个模型；不存在时静默。round-trip 写。"""
        document = read_document(self._path)
        section = document.get(_SECTION)
        providers = section.get("providers") if isinstance(section, Mapping) else None
        provider = (
            providers.get(provider_id) if isinstance(providers, Mapping) else None
        )
        models = provider.get("models") if isinstance(provider, Mapping) else None
        if isinstance(models, MutableMapping) and model_id in models:
            del models[model_id]
            write_document(self._path, document)

    def upsert_provider_field(
        self,
        provider_id: str,
        field: str,
        value: object,
        *,
        provider_defaults: Mapping[str, object],
    ) -> None:
        """设置供应商顶层字段(name/api_base 等)；段不存在则用默认建立（round-trip）。"""
        document = read_document(self._path)
        _ensure_provider(document, provider_id, provider_defaults)[field] = value
        write_document(self._path, document)

    def upsert_runtime_field(self, section: str, field: str, value: object) -> None:
        """写入应用级 ``llm.<section>`` 配置，保留其他段。"""
        document = read_document(self._path)
        runtime = _ensure_table(_ensure_table(document, _SECTION), section)
        runtime[field] = value
        write_document(self._path, document)


def _ensure_table(parent: dict[str, Any], key: str) -> dict[str, Any]:
    nested = parent.get(key)
    if not isinstance(nested, dict):
        nested = {}
        parent[key] = nested
    return nested


def _ensure_provider(
    document: dict[str, Any],
    provider_id: str,
    provider_defaults: Mapping[str, object],
) -> dict[str, Any]:
    providers = _ensure_table(_ensure_table(document, _SECTION), "providers")
    if provider_id not in providers or not isinstance(providers[provider_id], dict):
        providers[provider_id] = {
            **{
                key: value
                for key, value in provider_defaults.items()
                if value not in (None, "")
            },
            "models": {},
        }
    provider: dict[str, Any] = providers[provider_id]
    return provider
