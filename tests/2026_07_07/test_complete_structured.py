"""complete_structured：native / 受控降级 / strict 语义（ADR-0011 §3.7）。"""

import pytest

from forgecli.application.llm.gateway import (
    ChatMessage,
    CurrentModelSelection,
    DefaultLlmGateway,
    DefaultModelSelectionResolver,
    InMemoryModelCatalog,
    ModelCatalogEntry,
    ModelParams,
    ModelRequest,
    ModelResponseParseError,
    ProviderRegistry,
    RequestOrigin,
    StructuredModelRequest,
    TextBlock,
)
from forgecli.application.llm.model_ref import ModelRef
from forgecli.domain.conversation import MessageRole
from forgecli.infrastructure.llm.adapters import FakeModelProvider

_REF = ModelRef(provider="deepseek", model="deepseek-chat")
_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
}


def _gateway(
    provider: FakeModelProvider,
    *,
    native: bool,
    structured_retry_limit: int = 1,
) -> DefaultLlmGateway:
    registry = ProviderRegistry()
    registry.register(provider)
    catalog = InMemoryModelCatalog(
        (
            ModelCatalogEntry(
                provider="deepseek",
                model="deepseek-chat",
                context_window=65536,
                supports_structured_output=native,
            ),
        )
    )
    return DefaultLlmGateway(
        registry,
        resolver=DefaultModelSelectionResolver(catalog, current_model=_REF),
        structured_retry_limit=structured_retry_limit,
    )


def _structured(strict: bool = True) -> StructuredModelRequest:
    base = ModelRequest(
        request_id="req_1",
        session_id="sess_1",
        turn_id="turn_0001",
        origin=RequestOrigin.STRUCTURED_CLASSIFICATION,
        model_selection=CurrentModelSelection(),
        messages=(
            ChatMessage(role=MessageRole.USER, content=(TextBlock("给个标题"),)),
        ),
        params=ModelParams(),
    )
    return StructuredModelRequest(
        model_request=base, schema=_SCHEMA, schema_name="title_result", strict=strict
    )


def test_native_path_passes_schema_to_provider() -> None:
    provider = FakeModelProvider(content='{"title": "hello"}')
    gw = _gateway(provider, native=True)
    resp = gw.complete_structured(_structured())
    assert resp.data == {"title": "hello"}
    assert resp.validation_errors == ()
    assert provider.last_request is not None
    assert provider.last_request.response_schema == _SCHEMA
    assert provider.last_request.schema_name == "title_result"


def test_degraded_path_injects_schema_instruction_into_prompt() -> None:
    provider = FakeModelProvider(content='{"title": "hello"}')
    gw = _gateway(provider, native=False)
    resp = gw.complete_structured(_structured())
    assert resp.data == {"title": "hello"}
    assert provider.last_request is not None
    assert provider.last_request.response_schema is None
    assert provider.last_request.system_prompt is not None
    assert "title_result" in provider.last_request.system_prompt
    assert "JSON" in provider.last_request.system_prompt


def test_code_fenced_json_tolerated() -> None:
    provider = FakeModelProvider(content='```json\n{"title": "hi"}\n```')
    gw = _gateway(provider, native=False)
    assert gw.complete_structured(_structured()).data == {"title": "hi"}


def test_strict_invalid_json_raises_parse_error() -> None:
    provider = FakeModelProvider(content="这不是 JSON")
    gw = _gateway(provider, native=True)
    with pytest.raises(ModelResponseParseError) as excinfo:
        gw.complete_structured(_structured(strict=True))
    assert excinfo.value.provider == "deepseek"
    assert excinfo.value.request_id == "req_1"


def test_strict_schema_violation_raises_parse_error() -> None:
    provider = FakeModelProvider(content='{"not_title": 1}')
    gw = _gateway(provider, native=True)
    with pytest.raises(ModelResponseParseError):
        gw.complete_structured(_structured(strict=True))


def test_non_strict_returns_validation_errors_instead_of_raising() -> None:
    provider = FakeModelProvider(content='{"not_title": 1}')
    gw = _gateway(provider, native=True)
    resp = gw.complete_structured(_structured(strict=False))
    assert resp.validation_errors
    assert resp.data == {"not_title": 1}


def test_degraded_retry_is_bounded_and_does_not_switch_model() -> None:
    provider = FakeModelProvider(content="bad output")
    gw = _gateway(provider, native=False, structured_retry_limit=2)
    with pytest.raises(ModelResponseParseError):
        gw.complete_structured(_structured(strict=True))
    # retry 上限 = structured_retry_limit + 1 次调用，且始终同一 provider。
    assert provider.complete_calls == 3


def test_structured_response_carries_usage_and_identity() -> None:
    provider = FakeModelProvider(content='{"title": "t"}')
    gw = _gateway(provider, native=True)
    resp = gw.complete_structured(_structured())
    assert resp.request_id == "req_1"
    assert resp.provider == "deepseek"
    assert resp.model == "deepseek-chat"
    assert resp.usage.total_tokens > 0  # 估算 usage 也必须存在（§19）
