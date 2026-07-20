"""ModelRequest / params / messages / response DTO 的字段冻结与校验。"""

from __future__ import annotations

import pytest

from forgecli.application.llm.gateway import (
    ChatMessage,
    CurrentModelSelection,
    FinishReason,
    ModelParams,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    RequestOrigin,
    StructuredModelRequest,
    TextBlock,
    ThinkingConfig,
    ThinkingEffort,
    ThinkingMode,
)
from forgecli.domain.conversation import MessageRole


def _msg(text: str = "你好") -> ChatMessage:
    return ChatMessage(role=MessageRole.USER, content=(TextBlock(text),))


def _request(**overrides: object) -> ModelRequest:
    base: dict[str, object] = {
        "request_id": "req_1",
        "session_id": "sess_1",
        "turn_id": "turn_0001",
        "origin": RequestOrigin.CHAT,
        "model_selection": CurrentModelSelection(),
        "messages": (_msg(),),
        "params": ModelParams(),
    }
    base.update(overrides)
    return ModelRequest(**base)  # type: ignore[arg-type]


# ---- ModelRequest 字段冻结 ---------------------------------------------------


def test_request_builds_with_frozen_optional_fields_defaulted() -> None:
    req = _request()
    assert req.loop_step_id is None
    assert req.required_capabilities == ()
    assert req.min_context_window is None
    assert req.system_prompt is None
    assert req.tools == ()
    assert req.timeout_seconds is None
    assert req.budget_snapshot is None  # no-op 字段位先冻
    assert req.cancel_token is None  # 07-07 接线，字段位先冻
    assert dict(req.metadata) == {}


def test_request_has_no_stream_flag() -> None:
    # §3.3 / §6：流式与否由 complete / stream 决定，ModelRequest 不带 stream 标志。
    assert not hasattr(_request(), "stream")


def test_request_rejects_blank_ids() -> None:
    for blank in ("request_id", "session_id", "turn_id"):
        with pytest.raises(ValueError):
            _request(**{blank: "  "})


def test_request_rejects_nonpositive_window_and_timeout() -> None:
    with pytest.raises(ValueError):
        _request(min_context_window=0)
    with pytest.raises(ValueError):
        _request(timeout_seconds=0)


def test_metadata_rejects_credential_like_keys() -> None:
    # 验收：明文凭证字段不得出现在 DTO 中。
    for bad_key in ("api_key", "ApiKey", "DEEPSEEK_SECRET", "authorization"):
        with pytest.raises(ValueError):
            _request(metadata={bad_key: "x"})


def test_metadata_allows_safe_summary_keys() -> None:
    req = _request(metadata={"mode": "act", "command": "/run", "max_tokens": "8192"})
    assert req.metadata["mode"] == "act"


# ---- ModelParams / ThinkingConfig -------------------------------------------


def test_params_defaults_are_none_or_empty() -> None:
    p = ModelParams()
    assert (p.temperature, p.top_p, p.max_output_tokens) == (None, None, None)
    assert p.stop == ()
    assert dict(p.provider_options) == {}


def test_params_validate_ranges() -> None:
    with pytest.raises(ValueError):
        ModelParams(temperature=2.5)
    with pytest.raises(ValueError):
        ModelParams(top_p=1.5)
    with pytest.raises(ValueError):
        ModelParams(max_output_tokens=0)


def test_thinking_config_auto_default_and_budget_validation() -> None:
    cfg = ThinkingConfig()
    assert cfg.enabled is ThinkingMode.AUTO
    assert cfg.effort is ThinkingEffort.NONE
    with pytest.raises(ValueError):
        ThinkingConfig(budget_tokens=0)


def test_provider_options_namespaced_value_must_be_mapping() -> None:
    ModelParams(provider_options={"deepseek": {"foo": 1}})  # ok
    with pytest.raises(ValueError):
        ModelParams(provider_options={"deepseek": "not-a-namespace"})  # type: ignore[dict-item]


# ---- messages ----------------------------------------------------------------


def test_chat_message_requires_content() -> None:
    with pytest.raises(ValueError):
        ChatMessage(role=MessageRole.USER, content=())


# ---- response / usage --------------------------------------------------------


def test_usage_rejects_negative_tokens() -> None:
    ModelUsage(input_tokens=10, output_tokens=5)  # ok
    with pytest.raises(ValueError):
        ModelUsage(input_tokens=-1, output_tokens=0)


def test_response_builds_and_defaults_cached_false() -> None:
    resp = ModelResponse(
        request_id="req_1",
        provider="deepseek",
        model="deepseek-chat",
        content="hi",
        finish_reason=FinishReason.STOP,
        usage=ModelUsage(input_tokens=1, output_tokens=1, estimated=True),
        latency_ms=12.5,
    )
    assert resp.cached is False
    assert resp.tool_calls == ()
    assert resp.usage.estimated is True


def test_response_rejects_blank_provider_or_model() -> None:
    with pytest.raises(ValueError):
        ModelResponse(
            request_id="req_1",
            provider="",
            model="deepseek-chat",
            content="hi",
            finish_reason=FinishReason.STOP,
            usage=ModelUsage(input_tokens=1, output_tokens=1),
            latency_ms=1.0,
        )


# ---- structured request ------------------------------------------------------


def test_structured_request_requires_schema_name() -> None:
    with pytest.raises(ValueError):
        StructuredModelRequest(
            model_request=_request(origin=RequestOrigin.STRUCTURED_CLASSIFICATION),
            schema={"type": "object"},
            schema_name="  ",
        )
