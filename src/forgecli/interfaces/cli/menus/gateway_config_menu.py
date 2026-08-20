"""/config 下的应用级 LLM 网关运行时配置。

cache / circuit breaker / retry 持久化到 Forge home 的 ``llm.json``；
本菜单只负责呈现和调用 LlmConfigService，不解析或直接写 TOML。
"""

from __future__ import annotations

from collections.abc import Callable

from forgecli.application.interaction_ports import UserOutput
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.errors import ConfigError
from forgecli.application.menu import Choice, Menu


class GatewayConfigMenu:
    def __init__(self, service: LlmConfigService, output: UserOutput) -> None:
        self._service = service
        self._output = output

    def root_menu(self) -> Menu:
        return Menu(
            "网关运行时配置（应用级）",
            (
                Choice(
                    "响应缓存", preview=self._cache_summary, submenu=self.cache_menu
                ),
                Choice(
                    "熔断器",
                    preview=self._circuit_breaker_summary,
                    submenu=self.circuit_breaker_menu,
                ),
                Choice(
                    "重试策略", preview=self._retry_summary, submenu=self.retry_menu
                ),
            ),
        )

    def cache_menu(self) -> Menu:
        return Menu(
            "响应缓存（应用级 llm.json）",
            (
                Choice(
                    "启用响应缓存",
                    preview=lambda: self._bool_text(
                        self._service.cache_settings().enabled
                    ),
                    on_cycle=self._toggle(
                        "cache",
                        "enabled",
                        lambda: self._service.cache_settings().enabled,
                    ),
                ),
                self._text_choice(
                    "TTL（秒）",
                    "cache",
                    "ttl_seconds",
                    lambda: self._number(self._service.cache_settings().ttl_seconds),
                ),
                self._text_choice(
                    "最大缓存条目",
                    "cache",
                    "max_entries",
                    lambda: str(self._service.cache_settings().max_entries),
                ),
                self._text_choice(
                    "允许缓存的 origins",
                    "cache",
                    "origins",
                    lambda: ",".join(
                        origin.value
                        for origin in self._service.cache_settings().origins
                    ),
                ),
            ),
        )

    def circuit_breaker_menu(self) -> Menu:
        return Menu(
            "熔断器（应用级 llm.json）",
            (
                Choice(
                    "启用熔断器",
                    preview=lambda: self._bool_text(
                        self._service.circuit_breaker_settings().enabled
                    ),
                    on_cycle=self._toggle(
                        "circuit_breaker",
                        "enabled",
                        lambda: self._service.circuit_breaker_settings().enabled,
                    ),
                ),
                self._text_choice(
                    "连续失败阈值",
                    "circuit_breaker",
                    "failure_threshold",
                    lambda: str(
                        self._service.circuit_breaker_settings().failure_threshold
                    ),
                ),
                self._text_choice(
                    "冷却时间（秒）",
                    "circuit_breaker",
                    "cooldown_seconds",
                    lambda: self._number(
                        self._service.circuit_breaker_settings().cooldown_seconds
                    ),
                ),
            ),
        )

    def retry_menu(self) -> Menu:
        return Menu(
            "重试策略（应用级 llm.json）",
            (
                self._text_choice(
                    "429 短等阈值（秒）",
                    "retry",
                    "wait_threshold_seconds",
                    lambda: self._number(
                        self._service.retry_settings().wait_threshold_seconds
                    ),
                ),
            ),
        )

    def _text_choice(
        self,
        label: str,
        section: str,
        field: str,
        preview: Callable[[], str],
    ) -> Choice:
        return Choice(
            label,
            preview=preview,
            on_text=lambda raw: self._safe_set(section, field, raw),
            text_default=preview,
        )

    def _toggle(
        self,
        section: str,
        field: str,
        current: Callable[[], bool],
    ) -> Callable[[int], None]:
        return lambda _delta: self._safe_set(
            section, field, "false" if current() else "true"
        )

    def _safe_set(self, section: str, field: str, raw: str) -> None:
        try:
            self._service.set_runtime_field(section, field, raw)
        except ConfigError as exc:
            self._output.print(exc.message)

    def _cache_summary(self) -> str:
        settings = self._service.cache_settings()
        ttl = self._number(settings.ttl_seconds)
        return f"{self._bool_text(settings.enabled)} · TTL {ttl}s"

    def _circuit_breaker_summary(self) -> str:
        settings = self._service.circuit_breaker_settings()
        cooldown = self._number(settings.cooldown_seconds)
        return (
            f"{self._bool_text(settings.enabled)} · "
            f"阈值 {settings.failure_threshold} / {cooldown}s"
        )

    def _retry_summary(self) -> str:
        seconds = self._service.retry_settings().wait_threshold_seconds
        return f"429 短等 <= {self._number(seconds)}s"

    @staticmethod
    def _bool_text(value: bool) -> str:
        return "开启" if value else "关闭"

    @staticmethod
    def _number(value: float | None) -> str:
        return "不过期" if value is None else f"{value:g}"
