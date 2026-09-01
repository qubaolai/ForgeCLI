"""设置页拿到的配置面 (2026-08-28).

三件事在这里钉住:

1. 常规配置每一项都带中文名与说明 —— 页面不自己写文案;
2. 供应商表单的行由后端给 —— 页面不自己列字段;
3. **没配过的供应商也在列表里** —— 否则用户得先添加一个模型才能填端点, 而填端点正是
   添加模型之前要做的事.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from forgecli.application.llm import providers as provider_registry
from forgecli.application.llm.config.llm_config import (
    LlmConfig,
    ModelParams,
    ModelSpec,
    ProviderConfig,
)
from forgecli.domain.config import config_keys
from forgecli.domain.config.effective_config import EffectiveConfig
from forgecli.interfaces.web.app import create_app


def _provider(
    provider_id: str, *, models: tuple[ModelSpec, ...] = ()
) -> ProviderConfig:
    spec = provider_registry.require_known_provider(provider_id)
    return ProviderConfig.model_validate(
        {
            "id": provider_id,
            "name": spec.label,
            "api_base": spec.default_api_base,
            "api_key_env": spec.api_key_env or None,
            "models": models,
        }
    )


class FakeLlmConfig:
    def __init__(self) -> None:
        self.configured = (
            _provider(
                "deepseek",
                models=(
                    ModelSpec(
                        provider="deepseek",
                        id="deepseek-chat",
                        params=ModelParams.parse({"context_window": 64000}),
                    ),
                ),
            ),
        )

    def providers(self) -> tuple[ProviderConfig, ...]:
        return self.configured

    def effective_providers(self) -> tuple[ProviderConfig, ...]:
        configured = {item.id: item for item in self.configured}
        return tuple(
            configured.get(provider_id) or _provider(provider_id)
            for provider_id in sorted(provider_registry.REGISTRY)
        )

    def config(self) -> LlmConfig:
        return LlmConfig(providers=self.configured)

    def runtime_settings_snapshot(self) -> tuple[object, object, object]:
        from forgecli.application.llm.config.llm_config import (
            CacheSettings,
            CircuitBreakerSettings,
            RetrySettings,
        )

        return (CacheSettings(), CircuitBreakerSettings(), RetrySettings())


class FakeRuntime:
    def __init__(self) -> None:
        self.busy = False
        self.project = SimpleNamespace(project_id="demo")
        self.llm_config = FakeLlmConfig()
        self.config = EffectiveConfig.from_overrides({})
        self.availability = SimpleNamespace(is_available=lambda _id: False)

    def current_model(self) -> str:
        return ""

    def model_overrides(self) -> dict[str, str]:
        return {}

    def thinking_view(self) -> dict[str, object]:
        return {"model": "", "configured": False}


class FakeRegistry:
    def __init__(self, active: object) -> None:
        self.projects = SimpleNamespace(list_trusted=tuple)
        self.active = active

    def close(self) -> None:
        return None


def _client(tmp_path: Any) -> TestClient:
    app = create_app(
        registry=FakeRegistry(FakeRuntime()),  # type: ignore[arg-type]
        boot_token="known-token",
        static_dir=tmp_path,
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    client.get("/boot?token=known-token", follow_redirects=False)
    return client


# ---- 常规配置 ----


def test_every_setting_carries_its_chinese_name(tmp_path: Any) -> None:
    items = _client(tmp_path).get("/api/v1/settings").json()["items"]
    assert items
    for item in items:
        assert item["label"], item["key"]
        assert item["label"] != item["key"]


def test_settings_cover_the_whole_schema(tmp_path: Any) -> None:
    """少一项就是一个只能靠手改 config.json 才能碰的开关."""
    items = _client(tmp_path).get("/api/v1/settings").json()["items"]
    assert {item["key"] for item in items} == {key.name for key in config_keys.SCHEMA}


def test_logging_switches_are_visible_in_the_panel(tmp_path: Any) -> None:
    """回归: 这几项原先只有环境变量入口, 设置页里根本不存在."""
    items = _client(tmp_path).get("/api/v1/settings").json()["items"]
    keys = {item["key"] for item in items}
    assert config_keys.LOGGING_CONSOLE in keys
    assert config_keys.LOGGING_DIRECTORY in keys
    assert config_keys.LOGGING_MAX_VALUE_CHARS in keys
    assert config_keys.LOGGING_INCLUDE_HTTP in keys


def test_a_switch_says_what_turning_it_off_costs(tmp_path: Any) -> None:
    items = _client(tmp_path).get("/api/v1/settings").json()["items"]
    telemetry = next(
        item for item in items if item["key"] == config_keys.TELEMETRY_ENABLED
    )
    assert telemetry["kind"] == "bool"
    assert telemetry["help"]


# ---- 供应商 ----


def test_provider_form_rows_come_from_the_backend(tmp_path: Any) -> None:
    """页面照着渲染. 它自己列一份的话, 后端加字段时页面上只是少一行, 不会报错."""
    payload = _client(tmp_path).get("/api/v1/models").json()
    fields = payload["provider_fields"]
    assert [field["name"] for field in fields] == [
        "name",
        "api_base",
        "api_key_env",
        "timeout",
        "max_retries",
    ]
    assert all(field["label"] for field in fields)
    assert {field["kind"] for field in fields} == {"str", "int"}


def test_model_form_rows_come_from_the_backend(tmp_path: Any) -> None:
    """模型参数与供应商参数遵循同一规则，避免前端字段少于实际配置能力。"""
    payload = _client(tmp_path).get("/api/v1/models").json()
    fields = payload["model_fields"]
    assert [field["name"] for field in fields] == [
        "context_window",
        "max_tokens",
        "temperature",
        "top_p",
        "cost_per_1k_input",
        "cost_per_1k_output",
        "cost_per_1k_cached_input",
        "cost_per_1k_reasoning",
    ]
    assert all(field["label"] for field in fields)
    assert {field["kind"] for field in fields} == {"int", "float"}


def test_unconfigured_providers_are_still_editable(tmp_path: Any) -> None:
    """要能在添加第一个模型**之前**把端点和密钥变量名填好."""
    payload = _client(tmp_path).get("/api/v1/models").json()
    settings = {item["id"]: item for item in payload["provider_settings"]}
    assert set(settings) == set(provider_registry.REGISTRY)
    # 只有 deepseek 配过模型; 其余几家照样出现在可编辑列表里.
    assert [item["id"] for item in payload["items"]] == ["deepseek"]
    assert settings["openai"]["models"] == []
    assert settings["openai"]["timeout"] == 60


def test_provider_settings_carry_registry_defaults(tmp_path: Any) -> None:
    payload = _client(tmp_path).get("/api/v1/models").json()
    settings = {item["id"]: item for item in payload["provider_settings"]}
    assert settings["glm"]["api_key_env"] == "GLM_API_KEY"
    assert settings["deepseek"]["api_base"].endswith("/chat/completions")


def test_all_three_protocols_are_listed_with_their_support_flag(tmp_path: Any) -> None:
    """不支持的也要发给前端.

    看不到这一项会让人以为 Forge 不打算支持, 于是去找别的工具.
    """
    payload = _client(tmp_path).get("/api/v1/models").json()
    protocols = payload["provider_protocols"]

    assert [item["value"] for item in protocols] == [
        "openai_compatible",
        "anthropic_messages",
        "google_gemini",
    ]
    assert [item["supported"] for item in protocols] == [True, False, False]
    assert all(item["label"] for item in protocols)
