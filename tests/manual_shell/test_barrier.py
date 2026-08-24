"""失效屏障与"下一次 turn 被阻止" (ADR-0017 §10, §12, §15.4).

屏障的价值全在失败路径上: 清得掉缓存时它什么都不改变, 清不掉时它必须拦住 Agent.
"""

from __future__ import annotations

import pytest

from forgecli.application.manual_shell import ManualMutationBarrier

# ---- 基本行为 ----


def test_every_hook_runs_even_if_one_fails() -> None:
    """一个钩子炸掉不能让其余的不执行 —— 少清一处缓存也是过期事实."""
    barrier = ManualMutationBarrier()
    done: list[str] = []
    barrier.register("first", lambda: done.append("first"))
    barrier.register("boom", _explode)
    barrier.register("third", lambda: done.append("third"))

    outcome = barrier.trip()

    assert done == ["first", "third"]
    assert [name for name, _ in outcome.failures] == ["boom"]


def test_the_generation_advances_even_when_hooks_fail() -> None:
    """事实**确实**过期了, 这一点与回调跑没跑成功无关."""
    barrier = ManualMutationBarrier()
    barrier.register("boom", _explode)
    assert barrier.trip().generation == 1
    assert barrier.generation == 1


def test_duplicate_registration_is_rejected() -> None:
    """后一个静默覆盖前一个, 会让其中一处缓存永远清不掉."""
    barrier = ManualMutationBarrier()
    barrier.register("x", lambda: None)
    with pytest.raises(ValueError, match="重复注册"):
        barrier.register("x", lambda: None)


def test_a_clean_trip_does_not_block() -> None:
    barrier = ManualMutationBarrier()
    barrier.register("ok", lambda: None)
    barrier.trip()
    assert not barrier.blocked


def test_a_failed_trip_blocks_and_says_which_hook() -> None:
    barrier = ManualMutationBarrier()
    barrier.register("stale_view", _explode)
    barrier.trip()
    assert barrier.blocked
    assert "stale_view" in barrier.block_reason


def test_a_later_clean_trip_clears_the_block() -> None:
    """屏障不是一次性熔断: 下一次成功失效之后就该恢复."""
    barrier = ManualMutationBarrier()
    failing = {"boom": True}
    barrier.register("x", lambda: _explode() if failing["boom"] else None)
    barrier.trip()
    assert barrier.blocked
    failing["boom"] = False
    barrier.trip()
    assert not barrier.blocked


def _explode() -> None:
    raise RuntimeError("hook 失败")
