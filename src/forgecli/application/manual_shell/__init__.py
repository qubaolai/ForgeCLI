"""人工 Shell 模式 (ADR-0017): 与 Agent Shell 完全独立的信任通道."""

from forgecli.application.manual_shell.mutation_barrier import (
    BarrierOutcome,
    ManualMutationBarrier,
)
from forgecli.application.manual_shell.provider import (
    InteractiveShellProvider,
    InteractiveShellResolver,
    ManualShellContext,
    ManualShellObserver,
    ManualShellUnavailable,
    TerminalLease,
)
from forgecli.application.manual_shell.service import (
    ManualShellPhase,
    ManualShellService,
)

__all__ = [
    "BarrierOutcome",
    "InteractiveShellProvider",
    "InteractiveShellResolver",
    "ManualMutationBarrier",
    "ManualShellContext",
    "ManualShellObserver",
    "ManualShellPhase",
    "ManualShellService",
    "ManualShellUnavailable",
    "TerminalLease",
]
