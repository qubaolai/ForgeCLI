"""InMemoryCredentialPool 契约（ADR-0011 §7，2026-07-03 切片；接口随后续复核简化）。

覆盖：按序选取、冷却跳过、mark_failed/mark_succeeded、无凭证 / 全冷却报错、
凭证不被独占（并发获取互不阻塞、互不影响）。凭证只有可用 / 冷却两态。
"""

import pytest

from forgecli.application.llm.gateway.errors import ModelAuthError
from forgecli.infrastructure.llm.credentials import (
    EnvCredentialResolver,
    InMemoryCredentialPool,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _pool(
    env: dict[str, str], clock: _Clock | None = None
) -> tuple[InMemoryCredentialPool, _Clock]:
    clock = clock or _Clock()
    resolver = EnvCredentialResolver(getenv=env.get)
    return InMemoryCredentialPool(resolver, clock=clock), clock


def test_gets_first_available_ref_in_order() -> None:
    pool, _ = _pool({"KEY_A": "value-a", "KEY_B": "value-b"})
    credential = pool.get_credential("deepseek", ("KEY_A", "KEY_B"))
    assert credential.ref == "KEY_A"
    assert credential.value == "value-a"


def test_marked_failed_credential_skipped_during_cooldown() -> None:
    pool, _ = _pool({"KEY_A": "value-a", "KEY_B": "value-b"})
    pool.mark_failed("KEY_A", error_type="auth", retry_after=60.0)
    credential = pool.get_credential("deepseek", ("KEY_A", "KEY_B"))
    assert credential.ref == "KEY_B"


def test_cooldown_expires_after_retry_after() -> None:
    pool, clock = _pool({"KEY_A": "value-a"})
    pool.mark_failed("KEY_A", error_type="rate_limit", retry_after=30.0)
    clock.now += 31.0
    credential = pool.get_credential("deepseek", ("KEY_A",))
    assert credential.ref == "KEY_A"


def test_mark_succeeded_clears_cooldown() -> None:
    pool, _ = _pool({"KEY_A": "value-a"})
    pool.mark_failed("KEY_A", error_type="auth", retry_after=999.0)
    pool.mark_succeeded("KEY_A")
    credential = pool.get_credential("deepseek", ("KEY_A",))
    assert credential.ref == "KEY_A"


def test_no_refs_raises_auth_error() -> None:
    pool, _ = _pool({})
    with pytest.raises(ModelAuthError):
        pool.get_credential("local", ())


def test_all_cooling_raises_auth_error() -> None:
    pool, _ = _pool({"KEY_A": "value-a"})
    pool.mark_failed("KEY_A", error_type="auth", retry_after=999.0)
    with pytest.raises(ModelAuthError) as excinfo:
        pool.get_credential("deepseek", ("KEY_A",))
    assert "冷却" in excinfo.value.message


def test_unresolvable_refs_raise_last_auth_error() -> None:
    pool, _ = _pool({})
    with pytest.raises(ModelAuthError) as excinfo:
        pool.get_credential("deepseek", ("MISSING_A", "MISSING_B"))
    assert "MISSING_B" in excinfo.value.message


def test_credential_not_exclusive_concurrent_gets_both_succeed() -> None:
    """凭证不被独占（复核后移除 acquire/release）：并发获取互不阻塞。"""
    pool, _ = _pool({"KEY_A": "value-a"})
    first = pool.get_credential("deepseek", ("KEY_A",))
    second = pool.get_credential("deepseek", ("KEY_A",))
    assert first == second  # 同一凭证值，互不影响，无需归还
