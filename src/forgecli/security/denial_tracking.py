"""auto 模式下分类器拦截次数的计数 (ADR-0058 决策 4; 对应 denialTracking.ts).

分类器拦得太多有两种可能: 模型在反复试探, 或者分类器对这个任务判得太严. 两种都该让人
看一眼, 所以连续 3 次或累计 20 次之后退回问人, 而不是继续无声地拒.
"""

from __future__ import annotations

from dataclasses import replace

from forgecli.core.permissions import DenialTrackingState

__all__ = [
    "MAX_CONSECUTIVE_DENIALS",
    "MAX_TOTAL_DENIALS",
    "record_denial",
    "record_success",
    "should_fallback_to_prompting",
]

MAX_CONSECUTIVE_DENIALS = 3
MAX_TOTAL_DENIALS = 20


def record_denial(state: DenialTrackingState) -> DenialTrackingState:
    return DenialTrackingState(
        consecutive_denials=state.consecutive_denials + 1,
        total_denials=state.total_denials + 1,
    )


def record_success(state: DenialTrackingState) -> DenialTrackingState:
    """任何一次放行都打断"连续"计数; 累计不清."""
    if state.consecutive_denials == 0:
        return state
    return replace(state, consecutive_denials=0)


def should_fallback_to_prompting(state: DenialTrackingState) -> bool:
    return (
        state.consecutive_denials >= MAX_CONSECUTIVE_DENIALS
        or state.total_denials >= MAX_TOTAL_DENIALS
    )
