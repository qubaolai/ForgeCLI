"""OpenAICompatibleProvider：协议映射与错误归一化（ADR-0011 §6 / §12）。

全部经 httpx.MockTransport，默认不打网络；测试只用假 key 值。
"""

import json

import httpx
import pytest

from forgecli.application.llm.gateway import (
    Credential,
    FinishReason,
    ModelAuthError,
    ModelBadRequestError,
    ModelParams,
    ModelProviderInternalError,
    ModelRateLimitError,
    ModelResponseParseError,
    ModelTimeoutError,
    ModelUnavailableError,
    TextBlock,
    ThinkingConfig,
    ToolResultBlock,
    ToolSpec,
)
from forgecli.application.llm.gateway.messages import ChatMessage, ToolCall
from forgecli.application.llm.gateway.provider import ProviderRequest
from forgecli.application.llm.thinking import ThinkingEffortName
from forgecli.domain.conversation import MessageRole
from forgecli.infrastructure.llm.adapters import OpenAICompatibleProvider

_BASE = "https://fake.api.test"


def _provider(handler) -> OpenAICompatibleProvider:  # noqa: ANN001
    transport = httpx.MockTransport(handler)
    return OpenAICompatibleProvider(
        provider_id="deepseek",
        base_url=_BASE,
        client_factory=lambda: httpx.Client(transport=transport),
    )


def _request(
    *,
    params: ModelParams | None = None,
    thinking: ThinkingConfig | None = None,
    tools: tuple[ToolSpec, ...] = (),
    messages: tuple[ChatMessage, ...] | None = None,
    credential: Credential | None = None,
    system_prompt: str | None = None,
) -> ProviderRequest:
    return ProviderRequest(
        model="deepseek-chat",
        messages=messages
        or (ChatMessage(role=MessageRole.USER, content=(TextBlock("你好"),)),),
        params=params or ModelParams(),
        thinking=thinking,
        system_prompt=system_prompt,
        tools=tools,
        credential=credential
        or Credential(provider_id="deepseek", ref="FAKE", value="fake-key"),
    )


def _ok_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "id": "prov-req-1",
        "choices": [
            {
                "message": {"role": "assistant", "content": "回复"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }
    body.update(overrides)
    return body


def test_happy_path_maps_request_and_response() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json=_ok_body())

    response = _provider(handler).complete(
        _request(
            params=ModelParams(temperature=0.5, max_output_tokens=100),
            system_prompt="你是助手",
        )
    )
    assert captured["url"] == f"{_BASE}/chat/completions"
    assert captured["auth"] == "Bearer fake-key"
    payload = captured["payload"]
    assert payload["model"] == "deepseek-chat"
    assert payload["temperature"] == 0.5
    assert payload["max_tokens"] == 100
    assert payload["messages"][0] == {"role": "system", "content": "你是助手"}
    assert payload["messages"][1] == {"role": "user", "content": "你好"}
    assert response.content == "回复"
    assert response.finish_reason is FinishReason.STOP
    assert response.usage is not None
    assert response.usage.input_tokens == 5
    assert response.raw_metadata["provider_request_id"] == "prov-req-1"


def test_keyless_request_has_no_authorization_header() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=_ok_body())

    provider = OpenAICompatibleProvider(
        provider_id="local",
        base_url=_BASE,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    request = ProviderRequest(
        model="llama",
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock("hi"),)),),
        params=ModelParams(),
    )
    provider.complete(request)
    assert captured["auth"] is None


def test_tools_and_tool_result_messages_translated() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json=_ok_body())

    call = ToolCall(tool_call_id="call_1", name="read_file", arguments={"path": "a"})
    messages = (
        ChatMessage(role=MessageRole.USER, content=(TextBlock("读文件"),)),
        ChatMessage(role=MessageRole.ASSISTANT, content=(), tool_calls=(call,)),
        ChatMessage(
            role=MessageRole.TOOL,
            content=(ToolResultBlock(tool_call_id="call_1", content="文件内容"),),
        ),
    )
    tools = (ToolSpec(name="read_file", description="读取文件"),)
    _provider(handler).complete(_request(messages=messages, tools=tools))
    payload = captured["payload"]
    assert payload["tools"][0]["function"]["name"] == "read_file"
    assistant = payload["messages"][1]
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {
        "path": "a"
    }
    tool_msg = payload["messages"][2]
    assert tool_msg == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "文件内容",
    }


def test_tool_calls_in_response_normalized() -> None:
    body = _ok_body()
    body["choices"] = [
        {
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_9",
                        "type": "function",
                        "function": {
                            "name": "grep",
                            "arguments": '{"pattern": "foo"}',
                        },
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    ]
    response = _provider(lambda _req: httpx.Response(200, json=body)).complete(
        _request()
    )
    assert response.finish_reason is FinishReason.TOOL_CALLS
    assert response.tool_calls == (
        ToolCall(tool_call_id="call_9", name="grep", arguments={"pattern": "foo"}),
    )


def test_invalid_tool_arguments_raise_parse_error() -> None:
    body = _ok_body()
    body["choices"] = [
        {
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_9",
                        "function": {"name": "grep", "arguments": "{broken"},
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    ]
    with pytest.raises(ModelResponseParseError):
        _provider(lambda _req: httpx.Response(200, json=body)).complete(_request())


def test_cached_and_reasoning_tokens_normalized() -> None:
    body = _ok_body(
        usage={
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": 60},
            "completion_tokens_details": {"reasoning_tokens": 20},
        }
    )
    response = _provider(lambda _req: httpx.Response(200, json=body)).complete(
        _request()
    )
    assert response.usage is not None
    assert response.usage.cached_input_tokens == 60
    assert response.usage.reasoning_tokens == 20
    assert response.raw_metadata["cache_hit"] == "true"


def test_thinking_effort_mapped_to_reasoning_effort() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json=_ok_body())

    thinking = ThinkingConfig(enabled=True, effort=ThinkingEffortName("high"))
    _provider(handler).complete(_request(thinking=thinking))
    assert captured["payload"]["thinking"] == {"type": "enabled"}
    assert captured["payload"]["reasoning_effort"] == "high"


def test_thinking_off_sends_no_thinking_fields() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json=_ok_body())

    thinking = ThinkingConfig(enabled=False)
    _provider(handler).complete(_request(thinking=thinking))
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in captured["payload"]


def test_provider_options_passthrough_namespaced() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json=_ok_body())

    params = ModelParams(
        provider_options={
            "deepseek": {"custom_flag": True},
            "openai": {"other": 1},  # 非本 provider 命名空间：不透传
        }
    )
    _provider(handler).complete(_request(params=params))
    assert captured["payload"]["custom_flag"] is True
    assert "other" not in captured["payload"]


def test_response_schema_mapped_to_json_schema_format() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=_ok_body(
                choices=[
                    {
                        "message": {"role": "assistant", "content": '{"a": 1}'},
                        "finish_reason": "stop",
                    }
                ]
            ),
        )

    request = ProviderRequest(
        model="deepseek-chat",
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock("hi"),)),),
        params=ModelParams(),
        response_schema={"type": "object"},
        schema_name="result",
        strict_schema=True,
    )
    _provider(handler).complete(request)
    response_format = captured["payload"]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "result"
    assert response_format["json_schema"]["strict"] is True


# ---- 错误映射（§12）----


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ModelAuthError),
        (403, ModelAuthError),
        (429, ModelRateLimitError),
        (400, ModelBadRequestError),
        (404, ModelBadRequestError),
        (500, ModelProviderInternalError),
        (503, ModelProviderInternalError),
    ],
)
def test_http_status_normalized(status: int, expected: type[Exception]) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "detail"}})

    with pytest.raises(expected):
        _provider(handler).complete(_request())


def test_rate_limit_carries_retry_after() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, headers={"retry-after": "12.5"}, json={"error": {"message": "slow"}}
        )

    with pytest.raises(ModelRateLimitError) as excinfo:
        _provider(handler).complete(_request())
    assert excinfo.value.retry_after == 12.5


def test_timeout_normalized() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    with pytest.raises(ModelTimeoutError):
        _provider(handler).complete(_request())


def test_connection_failure_normalized() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ModelUnavailableError):
        _provider(handler).complete(_request())


def test_malformed_json_body_normalized() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    with pytest.raises(ModelResponseParseError):
        _provider(handler).complete(_request())


def test_error_message_never_contains_credential() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    with pytest.raises(ModelAuthError) as excinfo:
        _provider(handler).complete(_request())
    assert "fake-key" not in str(excinfo.value)
