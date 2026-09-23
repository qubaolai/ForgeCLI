import json

import httpx
import pytest

from forgecli.common.cancellation import CancelToken
from forgecli.core.model.catalog import ModelCatalogEntry
from forgecli.core.model.model_ref import ModelRef
from forgecli.core.model.origin import RequestOrigin
from forgecli.core.model.params import ModelParams
from forgecli.core.model.request import ModelRequest, StructuredModelRequest
from forgecli.core.model.resolved import ResolvedModel
from forgecli.llm.adapters.openai_compatible import OpenAICompatibleProvider
from forgecli.llm.config import ModelParams as ConfigModelParams
from forgecli.llm.gateway.default_gateway import DefaultLlmGateway
from forgecli.llm.gateway.errors import (
    ModelBadRequestError,
    ModelCancelledError,
    ModelResponseParseError,
)
from forgecli.llm.gateway.provider_registry import ProviderRegistry

SCHEMA = {
    "type": "object",
    "properties": {"should_block": {"type": "boolean"}},
    "required": ["should_block"],
    "additionalProperties": False,
}


def setup_gateway(native=False, json_object=False, reply=None):
    payloads = []
    token = CancelToken()

    def handle(request):
        body = json.loads(request.content)
        payloads.append(body)
        if reply is not None:
            override = reply(body, token)
            if override is not None:
                return override
        return httpx.Response(
            200,
            json={
                "id": "fake",
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"should_block":false}',
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 5,
                    "total_tokens": 10,
                },
            },
        )

    provider = OpenAICompatibleProvider(
        provider_id="deepseek",
        base_url="https://example.invalid/chat/completions",
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle)),
    )
    registry = ProviderRegistry()
    registry.register(provider)
    entry = ModelCatalogEntry(
        "deepseek",
        "test-model",
        10000,
        supports_structured_output=native,
        supports_json_object=json_object,
    )
    request = StructuredModelRequest(
        model_request=ModelRequest(
            "r",
            "s",
            "t",
            RequestOrigin.PERMISSION_CLASSIFIER,
            (),
            ModelParams(),
            system_prompt="Classify this command",
            cancel_token=token,
            resolved_model=ResolvedModel(ModelRef("deepseek", "test-model"), entry),
        ),
        schema=SCHEMA,
        schema_name="classification",
        strict=True,
    )
    return DefaultLlmGateway(registry, resolver=None), request, payloads


@pytest.mark.parametrize(
    "native,json_object,expected",
    [(True, True, "json_schema"), (False, True, "json_object"), (False, False, None)],
)
def test_structured_output_selects_declared_format(native, json_object, expected):
    gateway, request, payloads = setup_gateway(native, json_object)
    response = gateway.complete_structured(request)
    assert response.data == {"should_block": False}
    assert payloads[0].get("response_format", {}).get("type") == expected
    if not native:
        assert "should_block" in payloads[0]["messages"][0]["content"]


def test_unsupported_schema_uses_json_object_on_same_model():
    def reply(body, _token):
        if body.get("response_format", {}).get("type") == "json_schema":
            return httpx.Response(
                400,
                json={
                    "error": {"message": "This response_format type is unavailable now"}
                },
            )
        return None

    gateway, request, payloads = setup_gateway(True, True, reply)
    assert gateway.complete_structured(request).data == {"should_block": False}
    assert [p["response_format"]["type"] for p in payloads] == [
        "json_schema",
        "json_object",
    ]
    assert {p["model"] for p in payloads} == {"test-model"}


def test_unsupported_formats_end_in_validated_text():
    def reply(body, _token):
        if "response_format" in body:
            return httpx.Response(
                400, json={"error": {"message": "response_format is not supported"}}
            )
        return None

    gateway, request, payloads = setup_gateway(True, True, reply)
    assert gateway.complete_structured(request).data == {"should_block": False}
    assert len(payloads) == 3
    assert "response_format" not in payloads[-1]


def test_old_native_only_configuration_can_fall_back_without_json_mode():
    def reply(body, _token):
        if "response_format" in body:
            return httpx.Response(
                400,
                json={
                    "error": {"message": "This response_format type is unavailable now"}
                },
            )
        return None

    gateway, request, payloads = setup_gateway(True, False, reply)
    assert gateway.complete_structured(request).data == {"should_block": False}
    assert len(payloads) == 2
    assert "response_format" not in payloads[-1]


@pytest.mark.parametrize(
    "message",
    [
        "Invalid schema: required is missing",
        "Invalid response_format schema: additionalProperties required",
        "max_tokens is invalid",
    ],
)
def test_other_bad_requests_are_not_hidden(message):
    gateway, request, payloads = setup_gateway(
        True, True, lambda *_: httpx.Response(400, json={"error": {"message": message}})
    )
    with pytest.raises(ModelBadRequestError):
        gateway.complete_structured(request)
    assert len(payloads) == 1


def test_cancellation_prevents_format_retry():
    def reply(_body, token):
        token.cancel()
        return httpx.Response(
            400, json={"error": {"message": "response_format is unsupported"}}
        )

    gateway, request, payloads = setup_gateway(True, True, reply)
    with pytest.raises(ModelCancelledError):
        gateway.complete_structured(request)
    assert len(payloads) == 1


def test_json_object_is_still_schema_checked():
    def reply(*_):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"should_block":"false"}'},
                    }
                ]
            },
        )

    gateway, request, payloads = setup_gateway(False, True, reply)
    with pytest.raises(ModelResponseParseError):
        gateway.complete_structured(request)
    assert len(payloads) == 2


def test_unknown_models_do_not_claim_native_json_support():
    config = ConfigModelParams()
    assert not config.supports_structured_output
    assert not config.supports_json_object
    assert ConfigModelParams(supports_json_object=True).supports_json_object
