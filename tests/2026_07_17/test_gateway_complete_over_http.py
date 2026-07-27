"""非流式网关调用的端到端接缝：gateway + 真实 OpenAI adapter + mock HTTP。

既有测试把这条链路切成两半、各测各的：网关侧用 FakeModelProvider（只记录
ProviderRequest，不做协议映射），adapter 侧（2026-07-08）直接调
OpenAICompatibleProvider.complete()、不经过网关。本文件把两半接起来，只覆盖
**接缝**——即"网关构造 ProviderRequest -> 真实 adapter 映射成 HTTP -> 解析真实
响应体 -> 网关归一化成 ModelResponse"这一整条，不重复各半已覆盖的细节。

全部经 httpx.MockTransport，默认不打网络；只用假 key 值。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from forgecli.application.llm.gateway import (
    ChatMessage,
    CurrentModelSelection,
    DefaultLlmGateway,
    DefaultModelSelectionResolver,
    FinishReason,
    InMemoryModelCatalog,
    ModelAuthError,
    ModelCatalogEntry,
    ModelParams,
    ModelProviderInternalError,
    ModelRateLimitError,
    ModelRequest,
    ModelResponse,
    ProviderRegistry,
    ProviderRuntimeSettings,
    ProviderSettingsSource,
    RequestOrigin,
    TextBlock,
)
from forgecli.application.llm.model_ref import ModelRef
from forgecli.domain.conversation import MessageRole
from forgecli.infrastructure.llm.adapters import OpenAICompatibleProvider
from forgecli.infrastructure.llm.credentials import (
    EnvCredentialResolver,
    InMemoryCredentialPool,
)

_BASE = "https://fake.api.test"
_REF = ModelRef(provider="deepseek", model="deepseek-chat")


class _StubSettings(ProviderSettingsSource):
    def __init__(self, refs: tuple[str, ...], *, max_retries: int = 2) -> None:
        self._refs = refs
        self._max_retries = max_retries

    def settings_for(self, provider_id: str) -> ProviderRuntimeSettings:
        return ProviderRuntimeSettings(
            provider_id=provider_id,
            timeout_seconds=42.0,  # 钉死以断言超时确实传到 httpx
            max_retries=self._max_retries,
            credential_refs=self._refs,
        )


def _ok_body(**overrides: object) -> dict[str, Any]:
    body: dict[str, Any] = {
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


def _gateway(
    handler: Any,  # noqa: ANN401 - httpx handler 回调
    *,
    env: dict[str, str] | None = None,
    refs: tuple[str, ...] = ("KEY_A",),
    max_retries: int = 2,
) -> DefaultLlmGateway:
    """真实 adapter（mock transport）+ 真实凭证池 + 真实 resolver 装配的网关。"""
    transport = httpx.MockTransport(handler)
    provider = OpenAICompatibleProvider(
        provider_id="deepseek",
        base_url=_BASE,
        client_factory=lambda: httpx.Client(transport=transport),
    )
    registry = ProviderRegistry()
    registry.register(provider)
    catalog = InMemoryModelCatalog(
        (
            ModelCatalogEntry(
                provider="deepseek",
                model="deepseek-chat",
                context_window=65536,
                max_output_tokens=8192,
            ),
        )
    )
    environ = env if env is not None else {"KEY_A": "k1"}
    return DefaultLlmGateway(
        registry,
        resolver=DefaultModelSelectionResolver(catalog, current_model=_REF),
        settings_source=_StubSettings(refs, max_retries=max_retries),
        credential_pool=InMemoryCredentialPool(
            EnvCredentialResolver(getenv=environ.get)
        ),
    )


def _request(text: str = "你好") -> ModelRequest:
    return ModelRequest(
        request_id="req_1",
        session_id="sess_1",
        turn_id="turn_0001",
        origin=RequestOrigin.CHAT,
        model_selection=CurrentModelSelection(),
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock(text),)),),
        params=ModelParams(),
    )


def test_full_non_streaming_path_request_to_normalized_response() -> None:
    """接缝主用例：resolver 选出的模型上到 wire，真实响应体归一化回 ModelResponse。"""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_ok_body())

    response = _gateway(handler).complete(_request("你好"))

    # 出站：网关解析出的 model 与消息真的到了 HTTP payload。
    assert len(captured) == 1
    sent = json.loads(captured[0].content.decode("utf-8"))
    assert sent["model"] == "deepseek-chat"  # 由 resolver 解析，非请求硬编码
    assert sent["messages"] == [{"role": "user", "content": "你好"}]
    assert "stream" not in sent  # 非流式调用不带 stream 标志
    # api_base 即完整 endpoint（GLM 接入后语义）：adapter 不再自行拼接路径。
    assert str(captured[0].url) == _BASE

    # 入站：真实 OpenAI 响应体经 adapter 解析、经网关归一化成 ModelResponse。
    assert isinstance(response, ModelResponse)
    assert response.request_id == "req_1"  # 网关补的，adapter 不知道
    assert (response.provider, response.model) == ("deepseek", "deepseek-chat")
    assert response.content == "回复"
    assert response.finish_reason is FinishReason.STOP
    assert response.cached is False


def test_real_usage_from_body_flows_through_without_estimation() -> None:
    response = _gateway(lambda _req: httpx.Response(200, json=_ok_body())).complete(
        _request()
    )
    assert response.usage.estimated is False  # 供应商给了 usage，不估算
    assert response.usage.input_tokens == 5
    assert response.usage.output_tokens == 3
    assert response.usage.total_tokens == 8


def test_body_without_usage_triggers_gateway_estimation() -> None:
    """adapter 返回 usage=None -> 网关估算并标记 estimated（跨越两半的接缝）。"""
    body = _ok_body()
    del body["usage"]
    gw = _gateway(lambda _req: httpx.Response(200, json=body))
    response = gw.complete(_request())
    assert response.usage.estimated is True
    assert response.usage.input_tokens > 0
    assert response.usage.output_tokens > 0


def test_pooled_credential_reaches_authorization_header() -> None:
    """凭证池取出的 key 经网关注入、由 adapter 落到 HTTP 头（两半都没单独覆盖）。"""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_ok_body())

    _gateway(handler, env={"KEY_A": "secret-key-value"}).complete(_request())
    assert captured[0].headers["Authorization"] == "Bearer secret-key-value"


def test_merged_timeout_reaches_http_client() -> None:
    """provider 默认超时经网关合并后真的传给 httpx（_StubSettings 钉死 42s）。"""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_ok_body())

    _gateway(handler).complete(_request())
    assert captured[0].extensions["timeout"]["connect"] == 42.0


def test_http_error_normalized_with_gateway_context() -> None:
    """adapter 抛出的裸错误经网关补全安全上下文（provider/model/request_id）。"""
    body = {"error": {"message": "invalid key"}}
    gw = _gateway(lambda _req: httpx.Response(401, json=body), refs=("KEY_A",))
    with pytest.raises(ModelAuthError) as excinfo:
        gw.complete(_request())
    # adapter 只知道错误本身，这三个字段是网关补的。
    assert excinfo.value.provider == "deepseek"
    assert excinfo.value.model == "deepseek-chat"
    assert excinfo.value.request_id == "req_1"
    assert "invalid key" in str(excinfo.value)  # 安全摘要保留


def test_server_error_normalized_to_provider_internal_error() -> None:
    gw = _gateway(lambda _req: httpx.Response(500, json={}))
    with pytest.raises(ModelProviderInternalError):
        gw.complete(_request())


def test_429_over_http_surfaces_retry_after_after_retries_exhausted() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": {"message": "rate limited"}},
            headers={"retry-after": "99"},
        )

    # retry_after=99 远超短等阈值(5s) -> 冷却换凭证；只有一个 key，重试耗尽后抛出。
    gw = _gateway(handler, env={"KEY_A": "k1"}, refs=("KEY_A",), max_retries=1)
    with pytest.raises(ModelRateLimitError) as excinfo:
        gw.complete(_request())
    assert excinfo.value.retry_after == 99.0  # 真实 retry-after 头穿透两半


def test_credential_retry_switches_key_on_the_wire() -> None:
    """401 后网关换下一个凭证，第二次 HTTP 请求带的是新 key（跨接缝的重试环）。"""
    seen_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers["Authorization"])
        if len(seen_auth) == 1:
            return httpx.Response(401, json={"error": {"message": "bad key"}})
        return httpx.Response(200, json=_ok_body())

    gw = _gateway(
        handler,
        env={"KEY_A": "bad-key", "KEY_B": "good-key"},
        refs=("KEY_A", "KEY_B"),
    )
    response = gw.complete(_request())
    assert response.content == "回复"
    assert seen_auth == ["Bearer bad-key", "Bearer good-key"]
    assert response.raw_metadata["credential_retries"] == "1"


def test_unconfigured_credential_never_reaches_http() -> None:
    """未配置凭证时网关在发请求前就失败（§19：不发起任何网络请求）。"""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        calls.append(request)
        return httpx.Response(200, json=_ok_body())

    gw = _gateway(handler, env={}, refs=("MISSING_KEY",))
    with pytest.raises(ModelAuthError):
        gw.complete(_request())
    assert calls == []  # transport 从未被触达
