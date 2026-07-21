"""取消链路与 thinking 参数合并（ADR-0011 §3.5 / §8，2026-07-07 切片）。"""

import pytest

from forgecli.application.llm.gateway import (
    CancelToken,
    ChatMessage,
    CurrentModelSelection,
    DefaultLlmGateway,
    DefaultModelSelectionResolver,
    InMemoryModelCatalog,
    ModelCancelledError,
    ModelCatalogEntry,
    ModelParams,
    ModelRequest,
    ProviderRegistry,
    RequestOrigin,
    TextBlock,
    ThinkingMode,
)
from forgecli.application.llm.gateway.provider import (
    ProviderRequest,
    ProviderResponse,
)
from forgecli.application.llm.model_ref import ModelRef
from forgecli.application.llm.thinking import (
    ModelThinkingCapabilities,
    ModelThinkingSettings,
    ThinkingEffortName,
)
from forgecli.domain.conversation import MessageRole
from forgecli.infrastructure.llm.adapters import FakeModelProvider

_REF = ModelRef(provider="deepseek", model="deepseek-chat")


class _CancelMidCallProvider(FakeModelProvider):
    """模拟调用途中被取消：进入 complete 后信号才翻转（协作式取消）。"""

    def __init__(self, token: CancelToken) -> None:
        super().__init__(content="never returned")
        self._token = token

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.last_request = request
        self._token.cancel()
        return super().complete(request)


def _gateway(
    provider: FakeModelProvider,
    *,
    thinking_mode: ThinkingMode = ThinkingMode.AUTO,
    thinking_effort: ThinkingEffortName | None = None,
) -> DefaultLlmGateway:
    registry = ProviderRegistry()
    registry.register(provider)
    catalog = InMemoryModelCatalog(
        (
            ModelCatalogEntry(
                provider="deepseek",
                model="deepseek-chat",
                context_window=65536,
                thinking_capabilities=ModelThinkingCapabilities(
                    efforts=(thinking_effort,) if thinking_effort is not None else (),
                ),
                thinking_settings=ModelThinkingSettings(
                    mode=thinking_mode,
                    effort=thinking_effort,
                ),
            ),
        )
    )
    return DefaultLlmGateway(
        registry,
        resolver=DefaultModelSelectionResolver(catalog, current_model=_REF),
    )


def _request(
    *,
    origin: RequestOrigin = RequestOrigin.CHAT,
    cancel_token: CancelToken | None = None,
    params: ModelParams | None = None,
) -> ModelRequest:
    return ModelRequest(
        request_id="req_1",
        session_id="sess_1",
        turn_id="turn_0001",
        origin=origin,
        model_selection=CurrentModelSelection(),
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock("hi"),)),),
        params=params or ModelParams(),
        cancel_token=cancel_token,
    )


# ---- 取消 ----


def test_pre_cancelled_request_never_reaches_provider() -> None:
    provider = FakeModelProvider(content="ok")
    gw = _gateway(provider)
    token = CancelToken()
    token.cancel()
    with pytest.raises(ModelCancelledError) as excinfo:
        gw.complete(_request(cancel_token=token))
    assert provider.complete_calls == 0
    assert excinfo.value.request_id == "req_1"
    assert excinfo.value.provider == "deepseek"


def test_cooperative_cancel_inside_provider_propagates() -> None:
    token = CancelToken()
    provider = _CancelMidCallProvider(token)
    gw = _gateway(provider)
    with pytest.raises(ModelCancelledError):
        gw.complete(_request(cancel_token=token))


def test_cancel_token_reaches_provider_request() -> None:
    provider = FakeModelProvider(content="ok")
    gw = _gateway(provider)
    token = CancelToken()
    gw.complete(_request(cancel_token=token))
    assert provider.last_request is not None
    assert provider.last_request.cancel_token is token


# ---- thinking 合并（§3.5）----


def test_model_thinking_config_is_applied() -> None:
    provider = FakeModelProvider(content="ok")
    effort = ThinkingEffortName("medium")
    gw = _gateway(provider, thinking_effort=effort)
    gw.complete(_request(origin=RequestOrigin.PLAN))
    assert provider.last_request is not None
    thinking = provider.last_request.thinking
    assert thinking is not None
    assert thinking.enabled is True  # plan：auto -> 开（§3.5）
    assert thinking.effort == effort


def test_auto_thinking_off_for_chat_origin() -> None:
    provider = FakeModelProvider(content="ok")
    gw = _gateway(provider, thinking_mode=ThinkingMode.AUTO)
    gw.complete(_request(origin=RequestOrigin.CHAT))
    assert provider.last_request is not None
    thinking = provider.last_request.thinking
    assert thinking is not None
    assert thinking.enabled is False


def test_model_thinking_off_cannot_be_overridden_by_request() -> None:
    provider = FakeModelProvider(content="ok")
    gw = _gateway(provider, thinking_mode=ThinkingMode.OFF)
    gw.complete(_request(origin=RequestOrigin.PLAN))
    assert provider.last_request is not None
    thinking = provider.last_request.thinking
    assert thinking is not None
    assert thinking.enabled is False


def test_thinking_on_needs_no_separate_capability_flag() -> None:
    provider = FakeModelProvider(content="ok")
    gw = _gateway(provider, thinking_mode=ThinkingMode.ON)
    gw.complete(_request(origin=RequestOrigin.CHAT))
    assert provider.last_request is not None
    assert provider.last_request.thinking is not None
    assert provider.last_request.thinking.enabled is True


def test_model_request_params_reject_explicit_thinking() -> None:
    with pytest.raises(TypeError):
        ModelParams(thinking="on")  # type: ignore[call-arg]
