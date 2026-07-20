"""build_model_overrides：`[model_overrides.<origin>]` 段读取与校验（0702）。

只测「已加载 mapping -> 类型化 {RequestOrigin: ModelRef}」的转换与校验，不涉及文件 IO。
"""

from __future__ import annotations

import pytest

from forgecli.application.llm.gateway import ModelBadRequestError, RequestOrigin
from forgecli.application.llm.model_overrides import build_model_overrides
from forgecli.application.llm.model_ref import ModelRef


def test_empty_section_yields_empty_map() -> None:
    assert build_model_overrides({}) == {}


def test_valid_section_maps_origins_to_refs() -> None:
    section = {
        "plan": {"provider": "openai", "model": "gpt-4o"},
        "title": {"provider": "deepseek", "model": "deepseek-chat"},
    }
    overrides = build_model_overrides(section)
    assert overrides == {
        RequestOrigin.PLAN: ModelRef("openai", "gpt-4o"),
        RequestOrigin.TITLE: ModelRef("deepseek", "deepseek-chat"),
    }


def test_unknown_origin_rejected() -> None:
    with pytest.raises(ModelBadRequestError, match="未知用途覆盖 origin"):
        build_model_overrides({"nope": {"provider": "openai", "model": "gpt-4o"}})


def test_missing_provider_rejected() -> None:
    with pytest.raises(ModelBadRequestError, match="provider 与 model"):
        build_model_overrides({"plan": {"model": "gpt-4o"}})


def test_missing_model_rejected() -> None:
    with pytest.raises(ModelBadRequestError, match="provider 与 model"):
        build_model_overrides({"plan": {"provider": "openai"}})


def test_blank_values_rejected() -> None:
    with pytest.raises(ModelBadRequestError):
        build_model_overrides({"plan": {"provider": "  ", "model": "gpt-4o"}})
