"""归一化网关错误 -> 一行可行动提示（ADR-0011 §12）。

供 AgentLoop / AgentTurnService 等调用方复用：不向用户抛 traceback，
只输出安全摘要，也绝不替用户改选模型。
"""

from __future__ import annotations

from forgecli.application.llm.gateway.errors import (
    ModelAuthError,
    ModelBudgetExceededError,
    ModelCancelledError,
    ModelContextOverflowError,
    ModelGatewayError,
    ModelRateLimitError,
    ModelTimeoutError,
    ModelUnavailableError,
)


def actionable_message(exc: ModelGatewayError) -> str:
    """归一化错误 -> 一行可行动提示（§12 处理规则）。只含安全摘要。"""
    if isinstance(exc, ModelAuthError):
        hint = "认证失败：请检查对应供应商的 API Key 环境变量。"
    elif isinstance(exc, ModelRateLimitError):
        wait = f"（建议 {exc.retry_after:.0f}s 后重试）" if exc.retry_after else ""
        hint = f"触发限流{wait}：请稍后重试。"
    elif isinstance(exc, ModelContextOverflowError):
        hint = "上下文超出模型窗口：请精简输入，或用 /model 换更大窗口的模型。"
    elif isinstance(exc, ModelTimeoutError):
        hint = "调用超时：请稍后重试。"
    elif isinstance(exc, ModelUnavailableError):
        hint = "供应商暂不可用：请稍后重试，或用 /model 切换当前模型。"
    elif isinstance(exc, ModelBudgetExceededError):
        hint = "预算超限：本次调用被拒绝。"
    elif isinstance(exc, ModelCancelledError):
        hint = "本次调用已取消。"
    else:
        hint = "模型调用失败。"
    scope = f"[{exc.provider}:{exc.model}] " if exc.provider and exc.model else ""
    return f"{scope}{hint}（{exc.message}）"
