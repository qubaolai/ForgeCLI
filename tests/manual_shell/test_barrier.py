"""失效屏障与"下一次 turn 被阻止" (ADR-0017 §10, §12, §15.4).

屏障的价值全在失败路径上: 清得掉缓存时它什么都不改变, 清不掉时它必须拦住 Agent.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from forgecli.application.agent_turn.agent_turn_service import AgentTurnService
from forgecli.application.manual_shell import ManualMutationBarrier, ManualShellContext
from forgecli.application.prompt.system_prompt_builder import SystemPromptBuilder
from forgecli.application.security.risk_cache import RiskCache
from forgecli.domain.conversation.turn import TurnStatus
from forgecli.domain.intents import InputOrigin, ManualShellIntent
from forgecli.domain.security.risk import RiskLevel, RiskReport
from forgecli.interfaces.cli.shell_mode import ShellModeEntry
from support.fakes import FACTS, NoProjectInstructions

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
    barrier.register("risk_cache", _explode)
    barrier.trip()
    assert barrier.blocked
    assert "risk_cache" in barrier.block_reason


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


# ---- 真实缓存 ----


def test_the_risk_cache_is_actually_cleared() -> None:
    """缓存键里有脚本内容哈希, 但用户改的可能是脚本**依赖**的文件 —— 那不在键里."""
    cache = RiskCache()
    cache.put(
        "k",
        RiskReport(
            risk_level=RiskLevel.LOW,
            confidence=1.0,
            intent_aligned=True,
            summary="跑测试",
        ),
    )
    assert len(cache) == 1

    barrier = ManualMutationBarrier()
    barrier.register("risk_cache", cache.clear)
    barrier.trip()

    assert len(cache) == 0


# ---- 阻止下一次 turn ----


class _Session:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def record_user_message(
        self, text: str, *, turn_id: str, origin: InputOrigin = InputOrigin.PROGRAM
    ) -> None:
        self.messages.append(("user", text))

    def record_assistant_message(self, text: str, **kwargs: object) -> None:
        self.messages.append(("assistant", text))


def test_a_blocked_barrier_refuses_the_next_turn() -> None:
    """§12: 清不掉缓存就无法证明后续裁决基于当前事实, 宁可让用户重启."""
    barrier = ManualMutationBarrier()
    barrier.register("risk_cache", _explode)
    barrier.trip()

    def _never() -> object:
        raise AssertionError("被阻止的 turn 不该构造 loop")

    service = AgentTurnService(
        _Session(),  # type: ignore[arg-type]
        loop_factory=_never,  # type: ignore[arg-type]
        prompt_builder=SystemPromptBuilder(),
        runtime_facts=lambda: FACTS,
        instructions=NoProjectInstructions(),
        barrier=barrier,
    )
    response = service.handle_user_message("继续干活")

    assert response.status is TurnStatus.FAILED
    assert "risk_cache" in response.text


def test_the_refused_turn_is_still_recorded_in_pairs() -> None:
    """这一轮确实发生过, 只是被拒绝了 —— 事件日志不能只有一半."""
    barrier = ManualMutationBarrier()
    barrier.register("x", _explode)
    barrier.trip()
    session = _Session()

    AgentTurnService(
        session,  # type: ignore[arg-type]
        loop_factory=lambda: None,  # type: ignore[arg-type,return-value]
        prompt_builder=SystemPromptBuilder(),
        runtime_facts=lambda: FACTS,
        instructions=NoProjectInstructions(),
        barrier=barrier,
    ).handle_user_message("你好")

    assert [role for role, _ in session.messages] == ["user", "assistant"]


def test_no_barrier_means_no_blocking() -> None:
    """没接人工 Shell 时屏障永远不 trip, 也就永远不阻塞."""
    assert not ManualMutationBarrier().blocked


# ---- 界面必须说清后果 ----


def test_the_block_reason_reaches_the_user() -> None:
    """不能只记日志: 下一次 turn 会被拒, 用户看到的是"Forge 突然不干活了"."""
    barrier = ManualMutationBarrier()
    barrier.register("risk_cache", _explode)

    class _Service:
        def __init__(self, tripped: ManualMutationBarrier) -> None:
            self.barrier = tripped

        def enter(self, intent: object, context: object) -> object:
            self.barrier.trip()
            return _Result()

    class _Result:
        started = True
        summary = "返回 Forge · shell exit 0"

    console = Console(file=io.StringIO(), width=100, no_color=True)
    ShellModeEntry(
        console,
        _Service(barrier),  # type: ignore[arg-type]
        lambda: ManualShellContext(cwd="/ws"),
    ).enter(ManualShellIntent(raw_text="#"))

    stream = console.file
    assert isinstance(stream, io.StringIO)
    text = stream.getvalue()
    assert "已阻止后续 Agent turn" in text
    assert "risk_cache" in text


def _explode() -> None:
    raise RuntimeError("清不掉")
