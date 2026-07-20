"""ADR-0012 配置段解析与配置驱动装配：cache / circuit_breaker / retry。缺省
全部关闭或回落现行为。不含 rate_limit（客户端主动限流已移除）与 credentials
（凭证只支持环境变量，无 dotenv 开关）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.errors import ConfigValidationError
from forgecli.application.llm.gateway import (
    InMemoryResponseCache,
    NoopLlmCacheController,
    NoopProviderHealthRegistry,
    RequestOrigin,
    SlidingWindowHealthRegistry,
)
from forgecli.infrastructure.llm import (
    LlmConfigProviderSettingsSource,
    TomlLlmConfigStore,
)
from forgecli.interfaces.cli.llm_wiring import (
    _build_health_registry,
    _build_response_cache,
)

_FULL_TOML = """
[llm.cache]
enabled = true
ttl_seconds = 120
max_entries = 8
origins = ["title", "summary"]

[llm.circuit_breaker]
enabled = true
failure_threshold = 4
cooldown_seconds = 15

[llm.retry]
wait_threshold_seconds = 3.5
"""


def _service(tmp_path: Path, content: str = "") -> LlmConfigService:
    llm_toml = tmp_path / "llm.toml"
    llm_toml.write_text(content, encoding="utf-8")
    return LlmConfigService(TomlLlmConfigStore(llm_toml))


def test_defaults_when_sections_absent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert service.cache_settings().enabled is False
    assert service.circuit_breaker_settings().enabled is False
    assert service.retry_settings().wait_threshold_seconds == 5.0


def test_sections_parsed_with_values(tmp_path: Path) -> None:
    service = _service(tmp_path, _FULL_TOML)
    cache = service.cache_settings()
    assert cache.enabled is True
    assert cache.ttl_seconds == 120.0
    assert cache.max_entries == 8
    assert cache.origins == (RequestOrigin.TITLE, RequestOrigin.SUMMARY)
    breaker = service.circuit_breaker_settings()
    assert (breaker.failure_threshold, breaker.cooldown_seconds) == (4, 15.0)
    assert service.retry_settings().wait_threshold_seconds == 3.5


def test_unknown_origin_rejected(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        '[llm.cache]\nenabled = true\norigins = ["chat", "not_an_origin"]\n',
    )
    with pytest.raises(ConfigValidationError):
        service.cache_settings()


def test_invalid_types_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path, '[llm.circuit_breaker]\nenabled = "yes"\n')
    with pytest.raises(ConfigValidationError):
        service.circuit_breaker_settings()


def test_wiring_defaults_to_noop_when_disabled(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert isinstance(_build_response_cache(service), NoopLlmCacheController)
    assert isinstance(_build_health_registry(service), NoopProviderHealthRegistry)


def test_wiring_builds_real_implementations_when_enabled(tmp_path: Path) -> None:
    service = _service(tmp_path, _FULL_TOML)
    assert isinstance(_build_response_cache(service), InMemoryResponseCache)
    assert isinstance(_build_health_registry(service), SlidingWindowHealthRegistry)


def test_settings_source_carries_wait_threshold(tmp_path: Path) -> None:
    service = _service(tmp_path, "[llm.retry]\nwait_threshold_seconds = 3.5\n")
    source = LlmConfigProviderSettingsSource(service)
    assert source.settings_for("deepseek").wait_threshold_seconds == 3.5


def test_runtime_fields_can_be_updated_through_application_service(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.set_runtime_field("cache", "enabled", "true")
    service.set_runtime_field("cache", "ttl_seconds", "90")
    service.set_runtime_field("cache", "max_entries", "12")
    service.set_runtime_field("cache", "origins", "title, summary")
    service.set_runtime_field("circuit_breaker", "enabled", "true")
    service.set_runtime_field("circuit_breaker", "failure_threshold", "3")
    service.set_runtime_field("circuit_breaker", "cooldown_seconds", "20")
    service.set_runtime_field("retry", "wait_threshold_seconds", "2.5")

    assert service.cache_settings().enabled is True
    assert service.cache_settings().ttl_seconds == 90.0
    assert service.cache_settings().max_entries == 12
    assert service.cache_settings().origins == (
        RequestOrigin.TITLE,
        RequestOrigin.SUMMARY,
    )
    assert service.circuit_breaker_settings().enabled is True
    assert service.circuit_breaker_settings().failure_threshold == 3
    assert service.circuit_breaker_settings().cooldown_seconds == 20.0
    assert service.retry_settings().wait_threshold_seconds == 2.5


@pytest.mark.parametrize(
    ("section", "field", "raw"),
    [
        ("cache", "ttl_seconds", "0"),
        ("cache", "max_entries", "nope"),
        ("cache", "origins", "title,unknown"),
        ("circuit_breaker", "failure_threshold", "-1"),
        ("retry", "wait_threshold_seconds", "-0.1"),
        ("unknown", "field", "1"),
    ],
)
def test_runtime_field_updates_reject_invalid_values(
    tmp_path: Path, section: str, field: str, raw: str
) -> None:
    with pytest.raises(ConfigValidationError):
        _service(tmp_path).set_runtime_field(section, field, raw)
