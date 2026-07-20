"""build_catalog：内置基线 + 用户配置合并（ADR-0011 §4，2026-07-06 切片）。"""

from pathlib import Path

from forgecli.application.llm.catalog_builder import build_catalog
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.gateway import ThinkingEffort, ThinkingMode
from forgecli.application.llm.model_ref import ModelRef
from forgecli.infrastructure.llm.toml_store import TomlLlmConfigStore


def _config(tmp_path: Path, content: str = ""):  # noqa: ANN202
    path = tmp_path / "llm.toml"
    if content:
        path.write_text(content, encoding="utf-8")
    return LlmConfigService(TomlLlmConfigStore(path)).config()


def test_builtin_baseline_available_without_user_config(tmp_path: Path) -> None:
    catalog = build_catalog(_config(tmp_path))
    entry = catalog.get(ModelRef(provider="deepseek", model="deepseek-chat"))
    assert entry.context_window == 65536
    assert entry.supports_tool_calling is True
    assert entry.supports_structured_output is True


def test_user_config_overrides_baseline_window(tmp_path: Path) -> None:
    content = """
[llm.providers.deepseek.models.deepseek-chat]
context_window = 128000
max_tokens = 4096
"""
    catalog = build_catalog(_config(tmp_path, content))
    entry = catalog.get(ModelRef(provider="deepseek", model="deepseek-chat"))
    assert entry.context_window == 128000
    assert entry.max_output_tokens == 4096
    # 未覆盖的能力位沿用基线。
    assert entry.supports_tool_calling is True


def test_unknown_model_gets_conservative_default_window(tmp_path: Path) -> None:
    content = """
[llm.providers.openai.models.my-model]
temperature = 0.5
"""
    catalog = build_catalog(_config(tmp_path, content))
    entry = catalog.get(ModelRef(provider="openai", model="my-model"))
    assert entry.context_window == 32768
    assert entry.supports_tool_calling is False


def test_extra_capability_flags_map_to_catalog(tmp_path: Path) -> None:
    content = """
[llm.providers.openai.models.gpt-x]
extra = { supports_tools = true, supports_json_schema = true, supports_thinking = true }
"""
    catalog = build_catalog(_config(tmp_path, content))
    entry = catalog.get(ModelRef(provider="openai", model="gpt-x"))
    assert entry.supports_tool_calling is True
    assert entry.supports_structured_output is True
    assert entry.supports_thinking is True


def test_extra_deprecated_and_allowlist_flags(tmp_path: Path) -> None:
    content = """
[llm.providers.openai.models.old-model]
extra = { deprecated = true, allowlisted = false }
"""
    catalog = build_catalog(_config(tmp_path, content))
    entry = catalog.get(ModelRef(provider="openai", model="old-model"))
    assert entry.deprecated is True
    assert entry.allowlisted is False


def test_cost_fields_become_prices(tmp_path: Path) -> None:
    content = """
[llm.providers.deepseek.models.deepseek-chat]
cost_per_1k_input = 0.14
cost_per_1k_output = 0.28
"""
    catalog = build_catalog(_config(tmp_path, content))
    entry = catalog.get(ModelRef(provider="deepseek", model="deepseek-chat"))
    assert entry.input_price_per_1k == 0.14
    assert entry.output_price_per_1k == 0.28


def test_zero_cost_means_price_unknown(tmp_path: Path) -> None:
    content = """
[llm.providers.openai.models.gpt-x]
context_window = 8192
"""
    catalog = build_catalog(_config(tmp_path, content))
    entry = catalog.get(ModelRef(provider="openai", model="gpt-x"))
    assert entry.input_price_per_1k is None
    assert entry.output_price_per_1k is None


def test_model_thinking_fields_become_catalog_defaults(tmp_path: Path) -> None:
    content = """
[llm.providers.deepseek.models.deepseek-reasoner]
thinking_mode = "on"
thinking_effort = "high"
extra = { supports_thinking = true }
"""
    entry = build_catalog(_config(tmp_path, content)).get(
        ModelRef(provider="deepseek", model="deepseek-reasoner")
    )
    assert entry.thinking_mode is ThinkingMode.ON
    assert entry.thinking_effort is ThinkingEffort.HIGH
