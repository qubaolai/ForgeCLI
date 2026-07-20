"""TurnReplier 的 LlmGateway 实现（ADR-0011 §8 / 2026-07-07 集成切片）。

普通 chat turn 接入统一网关：构造 ModelRequest（origin=chat、CurrentModelSelection、
metadata 只带 mode 安全摘要）、调用 gateway.complete、经 UsageMeter 产出计量草稿。
归一化网关错误翻成一行可行动提示（§12），不向用户抛 traceback，也绝不改选模型。

阶段偏差（§17-4 / 07-07）：AgentLoop（ADR-0010）尚未落地，AgentTurnService 经本类
直连 gateway；后续 AgentLoop 落地时替换调用方，网关边界不变。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import MappingProxyType

from forgecli.application.agent_turn.replier import TurnReplier, TurnReply
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
from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.application.llm.gateway.messages import ChatMessage, TextBlock
from forgecli.application.llm.gateway.origin import RequestOrigin
from forgecli.application.llm.gateway.params import ModelParams
from forgecli.application.llm.gateway.request import CancelToken, ModelRequest
from forgecli.application.llm.gateway.selection import CurrentModelSelection
from forgecli.application.llm.metering import UsageMeter
from forgecli.domain.conversation import MessageRole, TurnStatus
from forgecli.domain.intents import SessionMode


def _new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:12]}"


class GatewayReplier(TurnReplier):
    """经 LlmGateway 完成一轮 chat 回复并产出 usage 草稿。"""

    def __init__(
        self,
        gateway: LlmGateway,
        usage_meter: UsageMeter,
        *,
        request_id_factory: Callable[[], str] = _new_request_id,
        cancel_token_factory: Callable[[], CancelToken | None] = lambda: None,
    ) -> None:
        self._gateway = gateway
        self._meter = usage_meter
        self._new_request_id = request_id_factory
        # 取消信号来源由装配方决定（REPL 的 Ctrl-C 接线属后续切片）；
        # 工厂返回 None 表示本轮不挂取消信号。
        self._new_cancel_token = cancel_token_factory

    def reply(
        self,
        *,
        text: str,
        mode: SessionMode,
        session_id: str,
        turn_id: str,
        history: tuple[ChatMessage, ...],
    ) -> TurnReply:
        request = ModelRequest(
            request_id=self._new_request_id(),
            session_id=session_id,
            turn_id=turn_id,
            origin=RequestOrigin.CHAT,
            model_selection=CurrentModelSelection(),
            messages=(
                *history,
                ChatMessage(role=MessageRole.USER, content=(TextBlock(text),)),
            ),
            params=ModelParams(),
            cancel_token=self._new_cancel_token(),
            # metadata 只放脱敏 mode 摘要（§3.3）；mode policy 不进请求。
            metadata=MappingProxyType({"mode": mode.value}),
        )
        try:
            response = self._gateway.complete(request)
        except ModelGatewayError as exc:
            return TurnReply(text=_actionable_message(exc), status=TurnStatus.FAILED)
        draft = self._meter.build_draft(request, response)
        return TurnReply(
            text=response.content, status=TurnStatus.COMPLETED, usage=draft
        )


def _actionable_message(exc: ModelGatewayError) -> str:
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
