"""/config 的应用级 cache / circuit breaker / retry 菜单。"""

from __future__ import annotations

from pathlib import Path

from forgecli.application.interaction_ports import UserOutput
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.menu import Choice, Menu
from forgecli.infrastructure.llm import TomlLlmConfigStore
from forgecli.interfaces.cli.menus.gateway_config_menu import GatewayConfigMenu


class _Output(UserOutput):
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, message: str) -> None:
        self.lines.append(message)


def _menu(tmp_path: Path) -> tuple[GatewayConfigMenu, LlmConfigService, _Output]:
    service = LlmConfigService(TomlLlmConfigStore(tmp_path / "llm.toml"))
    output = _Output()
    return GatewayConfigMenu(service, output), service, output


def _find(menu: Menu, label: str) -> Choice:
    return next(choice for choice in menu.choices if choice.label == label)


def test_root_exposes_all_app_level_gateway_sections(tmp_path: Path) -> None:
    menu, _, _ = _menu(tmp_path)
    labels = {choice.label for choice in menu.root_menu().choices}
    assert labels == {"响应缓存", "熔断器", "重试策略"}


def test_cache_menu_updates_llm_toml(tmp_path: Path) -> None:
    menu, service, _ = _menu(tmp_path)
    cache = menu.cache_menu()
    enabled = _find(cache, "启用响应缓存")
    assert enabled.preview is not None and enabled.preview() == "关闭"
    assert enabled.on_cycle is not None
    enabled.on_cycle(1)

    ttl = _find(cache, "TTL（秒）")
    origins = _find(cache, "允许缓存的 origins")
    assert ttl.on_text is not None and origins.on_text is not None
    ttl.on_text("45")
    origins.on_text("title,summary")

    assert service.cache_settings().enabled is True
    assert service.cache_settings().ttl_seconds == 45.0
    assert [origin.value for origin in service.cache_settings().origins] == [
        "title",
        "summary",
    ]
    text = (tmp_path / "llm.toml").read_text(encoding="utf-8")
    assert "[llm.cache]" in text


def test_circuit_breaker_and_retry_menus_update_settings(tmp_path: Path) -> None:
    menu, service, _ = _menu(tmp_path)
    breaker = menu.circuit_breaker_menu()
    enabled = _find(breaker, "启用熔断器")
    threshold = _find(breaker, "连续失败阈值")
    cooldown = _find(breaker, "冷却时间（秒）")
    assert enabled.on_cycle is not None
    assert threshold.on_text is not None
    assert cooldown.on_text is not None
    enabled.on_cycle(1)
    threshold.on_text("4")
    cooldown.on_text("15")

    retry = _find(menu.retry_menu(), "429 短等阈值（秒）")
    assert retry.on_text is not None
    retry.on_text("3.5")

    assert service.circuit_breaker_settings().enabled is True
    assert service.circuit_breaker_settings().failure_threshold == 4
    assert service.circuit_breaker_settings().cooldown_seconds == 15.0
    assert service.retry_settings().wait_threshold_seconds == 3.5


def test_invalid_value_is_reported_without_writing(tmp_path: Path) -> None:
    menu, service, output = _menu(tmp_path)
    ttl = _find(menu.cache_menu(), "TTL（秒）")
    assert ttl.on_text is not None
    ttl.on_text("0")

    assert output.lines and "必须为正数" in output.lines[-1]
    assert service.cache_settings().ttl_seconds == 600.0
