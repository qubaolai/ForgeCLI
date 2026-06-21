"""模型配置（封闭供应商 + 配置驱动模型 + tomlkit round-trip）验收测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.llm.config.service import FileLlmConfigService
from forgecli.application.llm.errors import (
    ConfigReadError,
    ConfigValidationError,
    UnknownProvider,
)
from forgecli.infrastructure.llm.config.toml_store import TomlLlmConfigStore

EXAMPLE = """
[llm.providers.deepseek]
name = "DeepSeek"
api_base = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"
timeout = 90
max_retries = 3

[llm.providers.deepseek.models]
deepseek-chat = { context_window = 65536, temperature = 0.7 }
deepseek-reasoner = { context_window = 65536, extra = { reasoning = true } }

[llm.providers.mimo]
name = "MiMo"
api_base = "https://api.mimo.example/v1"

[llm.providers.mimo.models]
mimo-7b = { context_window = 32768, max_tokens = 4096, top_p = 0.9 }

# 未知供应商：没有 adapter，应被忽略而不报错
[llm.providers.openai]
name = "OpenAI"
[llm.providers.openai.models]
gpt-4o = { context_window = 128000 }
"""


def _service(
    tmp_path: Path, text: str | None = None
) -> tuple[FileLlmConfigService, Path]:
    path = tmp_path / ".forge" / "llm.toml"
    if text is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return FileLlmConfigService(TomlLlmConfigStore(path)), path


# ---- 读取 / 解析 ----


def test_empty_when_no_file(tmp_path: Path) -> None:
    service, path = _service(tmp_path)
    config = service.config()
    assert config.providers == ()
    assert not path.exists()


def test_parses_known_providers_and_models(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, EXAMPLE)
    config = service.config()

    assert {p.id for p in config.providers} == {"deepseek", "mimo"}
    deepseek = config.provider("deepseek")
    assert deepseek is not None
    assert deepseek.timeout == 90
    assert deepseek.max_retries == 3
    assert {m.id for m in deepseek.models} == {"deepseek-chat", "deepseek-reasoner"}

    chat = config.model("deepseek", "deepseek-chat")
    assert chat is not None
    assert chat.params.context_window == 65536
    assert chat.params.temperature == 0.7


def test_vendor_extra_is_passed_through(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, EXAMPLE)
    reasoner = service.config().model("deepseek", "deepseek-reasoner")
    assert reasoner is not None
    assert reasoner.params.extra == {"reasoning": True}


def test_unknown_provider_section_is_ignored(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, EXAMPLE)
    config = service.config()
    # openai 没有 adapter -> 被忽略，不进入有效配置，也不报错
    assert config.provider("openai") is None


# ---- 封闭供应商 ----


def test_add_model_to_unknown_provider_is_rejected(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    with pytest.raises(UnknownProvider):
        service.add_model("openai", "gpt-4o", {"context_window": 128000})


# ---- 参数校验 ----


def test_invalid_temperature_is_rejected(tmp_path: Path) -> None:
    service, path = _service(tmp_path)
    with pytest.raises(ConfigValidationError):
        service.add_model("deepseek", "bad", {"temperature": 5})
    assert not path.exists()


def test_extra_must_be_object(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    with pytest.raises(ConfigValidationError):
        service.add_model("deepseek", "bad", {"extra": "not-an-object"})


# ---- 写入：round-trip 保留注释 / 其余内容 ----


def test_add_model_creates_provider_section_with_defaults(tmp_path: Path) -> None:
    service, path = _service(tmp_path)

    service.add_model(
        "deepseek",
        "deepseek-chat",
        {"context_window": 65536, "max_tokens": 8192, "temperature": 0.7},
    )

    assert path.exists()
    reopened = FileLlmConfigService(TomlLlmConfigStore(path))
    chat = reopened.config().model("deepseek", "deepseek-chat")
    assert chat is not None
    assert chat.params.max_tokens == 8192
    # provider 段用注册表默认补齐
    deepseek = reopened.config().provider("deepseek")
    assert deepseek is not None
    assert deepseek.api_base == "https://api.deepseek.com"
    assert deepseek.api_key_env == "DEEPSEEK_API_KEY"


def test_write_preserves_comments_and_other_models(tmp_path: Path) -> None:
    seed = (
        "# 我的手写注释\n"
        "[llm.providers.deepseek]\n"
        'name = "DeepSeek"\n'
        'api_base = "https://api.deepseek.com"\n\n'
        "[llm.providers.deepseek.models]\n"
        "deepseek-chat = { context_window = 65536, max_tokens = 8192 }\n"
    )
    service, path = _service(tmp_path, seed)

    service.add_model("deepseek", "deepseek-reasoner", {"context_window": 65536})

    text = path.read_text(encoding="utf-8")
    assert "# 我的手写注释" in text  # round-trip 保留注释
    assert "deepseek-chat" in text  # 旧模型仍在
    assert "deepseek-reasoner" in text  # 新模型已加
    models = {m.id for m in service.config().provider("deepseek").models}
    assert models == {"deepseek-chat", "deepseek-reasoner"}


def test_remove_model(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, EXAMPLE)
    service.remove_model("deepseek", "deepseek-reasoner")
    models = {m.id for m in service.config().provider("deepseek").models}
    assert models == {"deepseek-chat"}


# ---- 语法错误友好处理 ----


def test_broken_toml_raises_friendly_read_error(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, "this is = = not valid ===")
    with pytest.raises(ConfigReadError) as excinfo:
        service.config()
    assert "语法错误" in excinfo.value.message
