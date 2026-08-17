"""ManualShellService 的编排与失效屏障 (ADR-0017 §5, §7, §10, §12)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from forgecli.application.manual_shell import (
    InteractiveShellProvider,
    InteractiveShellResolver,
    ManualMutationBarrier,
    ManualShellContext,
    ManualShellPhase,
    ManualShellService,
    ManualShellUnavailable,
    NullManualShellObserver,
    TerminalLease,
)
from forgecli.domain.intents import ManualShellIntent
from forgecli.domain.manual_shell.request import ManualShellRequest
from forgecli.domain.manual_shell.result import ManualShellResult

INTENT = ManualShellIntent(raw_text="#")
CONTEXT = ManualShellContext(cwd="/ws")


class _Resolver(InteractiveShellResolver):
    def __init__(self, error: str | None = None) -> None:
        self._error = error

    def resolve(self, context: ManualShellContext) -> ManualShellRequest:
        if self._error:
            raise ManualShellUnavailable(self._error)
        return ManualShellRequest(
            shell_executable="/bin/zsh", argv=("/bin/zsh", "-i"), cwd=context.cwd
        )


class _Provider(InteractiveShellProvider):
    def __init__(
        self, *, exit_code: int | None = 0, signal: int | None = None, boom: str = ""
    ) -> None:
        self.calls = 0
        self._exit_code = exit_code
        self._signal = signal
        self._boom = boom

    def open(self, request: ManualShellRequest) -> ManualShellResult:
        self.calls += 1
        if self._boom:
            raise ManualShellUnavailable(self._boom)
        return ManualShellResult(
            started_at="t0",
            finished_at="t1",
            exit_code=self._exit_code,
            signal=self._signal,
        )


class _Lease(TerminalLease):
    def __init__(self, *, boom: bool = False) -> None:
        self.acquired = 0
        self.released = 0
        self._boom = boom

    @contextmanager
    def acquire(self) -> Iterator[None]:
        self.acquired += 1
        try:
            if self._boom:
                raise RuntimeError("终端交接炸了")
            yield
        finally:
            self.released += 1


def _service(**kwargs: object) -> ManualShellService:
    defaults: dict[str, object] = {
        "resolver": _Resolver(),
        "provider": _Provider(),
        "lease": _Lease(),
    }
    defaults.update(kwargs)
    return ManualShellService(
        defaults["resolver"],  # type: ignore[arg-type]
        defaults["provider"],  # type: ignore[arg-type]
        defaults["lease"],  # type: ignore[arg-type]
        defaults.get("observer") or NullManualShellObserver(),  # type: ignore[arg-type]
        barrier=defaults.get("barrier"),  # type: ignore[arg-type]
    )


# ---- 正常路径 ----


def test_a_normal_session_returns_the_exit_code() -> None:
    result = _service().enter(INTENT, CONTEXT)
    assert result.exit_code == 0
    assert result.started


def test_a_signal_exit_is_not_reported_as_a_failed_command() -> None:
    """被信号杀掉的 Shell 没跑完, 说成"命令失败"是另一回事 (§4)."""
    result = _service(provider=_Provider(exit_code=None, signal=9)).enter(
        INTENT, CONTEXT
    )
    assert "信号 9" in result.summary
    assert "exit" not in result.summary


def test_the_phase_returns_to_idle() -> None:
    service = _service()
    service.enter(INTENT, CONTEXT)
    assert service.phase is ManualShellPhase.PROMPT_IDLE


def test_reentering_is_a_fresh_session() -> None:
    """§6.2: 每次进入都是新 Shell, 不继承上次的状态."""
    provider = _Provider()
    service = _service(provider=provider)
    service.enter(INTENT, CONTEXT)
    service.enter(INTENT, CONTEXT)
    assert provider.calls == 2


# ---- 终端租约 ----


def test_the_lease_is_released_on_the_happy_path() -> None:
    lease = _Lease()
    _service(lease=lease).enter(INTENT, CONTEXT)
    assert (lease.acquired, lease.released) == (1, 1)


def test_the_lease_is_released_when_the_provider_refuses() -> None:
    """§7: 恢复必须走 finally, 覆盖启动失败."""
    lease = _Lease()
    result = _service(lease=lease, provider=_Provider(boom="没有 PTY")).enter(
        INTENT, CONTEXT
    )
    assert lease.released == 1
    assert not result.started
    assert "没有 PTY" in result.summary


def test_a_lease_failure_still_releases() -> None:
    lease = _Lease(boom=True)
    with pytest.raises(RuntimeError):
        _service(lease=lease).enter(INTENT, CONTEXT)
    assert lease.released == 1


def test_the_terminal_is_never_taken_when_no_shell_resolves() -> None:
    """解析不出 Shell 就不该动终端 —— §4 要求"不进入半初始化状态"."""
    lease = _Lease()
    result = _service(lease=lease, resolver=_Resolver(error="找不到 Shell")).enter(
        INTENT, CONTEXT
    )
    assert lease.acquired == 0
    assert not result.started


# ---- 失效屏障 ----


def test_the_barrier_trips_after_a_normal_session() -> None:
    barrier = ManualMutationBarrier()
    cleared: list[str] = []
    barrier.register("risk_cache", lambda: cleared.append("risk_cache"))

    _service(barrier=barrier).enter(INTENT, CONTEXT)

    assert cleared == ["risk_cache"]
    assert barrier.generation == 1


def test_the_barrier_trips_even_when_the_shell_never_started() -> None:
    """启动失败不等于什么都没发生: rc 文件可能已经跑过了 (§10)."""
    barrier = ManualMutationBarrier()
    _service(barrier=barrier, resolver=_Resolver(error="x")).enter(INTENT, CONTEXT)
    assert barrier.generation == 1


def test_the_barrier_trips_even_when_the_lease_explodes() -> None:
    barrier = ManualMutationBarrier()
    with pytest.raises(RuntimeError):
        _service(barrier=barrier, lease=_Lease(boom=True)).enter(INTENT, CONTEXT)
    assert barrier.generation == 1


# ---- 状态机 ----


def test_entering_twice_at_once_is_refused() -> None:
    """§3: 只有 PROMPT_IDLE 能进. 不允许"暂停一半 Agent turn 再进 Shell"."""
    service = _service()

    class _Reentrant(InteractiveShellProvider):
        def open(self, request: ManualShellRequest) -> ManualShellResult:
            with pytest.raises(ManualShellUnavailable, match="空闲提示符"):
                service.enter(INTENT, CONTEXT)
            return ManualShellResult(started_at="t0", finished_at="t1", exit_code=0)

    service._provider = _Reentrant()  # type: ignore[attr-defined]
    service.enter(INTENT, CONTEXT)
