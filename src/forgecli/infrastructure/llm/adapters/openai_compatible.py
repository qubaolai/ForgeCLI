"""OpenAI-compatible provider adapter（ADR-0011 §2 / §6 / 2026-07-08 切片）。

deepseek / mimo / openai / local 均走 OpenAI chat-completions 协议，本 adapter
按 provider 实例化（provider_id + base_url 不同）。约束（§3.2）：

    - 只做「统一请求 <-> OpenAI 协议」映射；不读写 session / 配置 / 事件 / 预算。
    - 不读环境变量：凭证由 gateway 经 CredentialPool 注入 ProviderRequest.credential；
      keyless（local）时无 Authorization 头。
    - 错误归一化为统一 ModelGatewayError 类型（§12），消息只含安全摘要。
    - cancel_token（§8 / §9）：非流式在发送前检查；流式在读行间隙检查并
      close 在途响应，不等待自然超时。

"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator, Mapping
from typing import Any

import httpx

from forgecli.application.llm.gateway.errors import (
    ModelAuthError,
    ModelBadRequestError,
    ModelCancelledError,
    ModelContextOverflowError,
    ModelProviderInternalError,
    ModelRateLimitError,
    ModelResponseParseError,
    ModelTimeoutError,
    ModelUnavailableError,
)
from forgecli.application.llm.gateway.provider import (
    ModelProvider,
    ProviderCapabilities,
    ProviderRequest,
    ProviderResponse,
)
from forgecli.domain.conversation.message import ChatMessage, TextBlock, ToolResultBlock
from forgecli.domain.model.provider_spec import ThinkingDialect
from forgecli.domain.model.response import FinishReason, ModelUsage
from forgecli.domain.model.streaming import ProviderStreamChunk, ToolCallDelta
from forgecli.domain.tool.tool_call import ToolCall
from forgecli.shared.observability.log import get_log

_FINISH_REASONS: dict[str, FinishReason] = {
    "stop": FinishReason.STOP,
    "length": FinishReason.LENGTH,
    "tool_calls": FinishReason.TOOL_CALLS,
    "content_filter": FinishReason.CONTENT_FILTER,
}

# adapter/provider 级协议能力（§3.2）：OpenAI chat-completions 协议本身支持这些
# 调用形态；单个模型是否具备相应能力以 ModelCatalogService 为准（§4）。
_PROTOCOL_CAPABILITIES = ProviderCapabilities(
    supports_streaming=True,
    supports_tools=True,
    supports_structured_output=True,
)


_log = get_log(__name__)


def _default_client_factory() -> httpx.Client:
    return httpx.Client()


class OpenAICompatibleProvider(ModelProvider):
    """OpenAI chat-completions 协议映射 adapter。"""

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        client_factory: Callable[[], httpx.Client] | None = None,
        capabilities: ProviderCapabilities | None = None,
        thinking_dialect: ThinkingDialect = ThinkingDialect.EFFORT,
    ) -> None:
        if not base_url.strip():
            raise ValueError("OpenAICompatibleProvider.base_url 不能为空")
        self._provider_id = provider_id
        self._base_url = base_url.rstrip("/")
        self._client_factory = client_factory or _default_client_factory
        self._capabilities = capabilities or _PROTOCOL_CAPABILITIES
        # thinking 方言（ADR-0012 §4）：wiring 按 provider 注册表给定。
        self._thinking_dialect = thinking_dialect

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    # ---- 非流式 ----

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self._raise_if_cancelled(request)
        payload = self._payload(request, stream=False)
        _log.debug("http.request", url=self._base_url, payload=payload)
        started = time.perf_counter()
        with self._client_factory() as client:
            try:
                response = client.post(
                    self._base_url,
                    json=payload,
                    headers=self._headers(request),
                    timeout=request.timeout_seconds,
                )
            except httpx.TimeoutException as exc:
                _log.warning(
                    "http.timeout",
                    url=self._base_url,
                    timeout_seconds=request.timeout_seconds,
                    elapsed_ms=(time.perf_counter() - started) * 1000.0,
                    message=str(exc),
                )
                raise ModelTimeoutError(f"provider 请求超时: {exc}") from exc
            except httpx.TransportError as exc:
                _log.warning(
                    "http.transport_error",
                    url=self._base_url,
                    error=type(exc).__name__,
                    elapsed_ms=(time.perf_counter() - started) * 1000.0,
                    message=str(exc),
                )
                raise ModelUnavailableError(f"provider 连接失败: {exc}") from exc
            _log.info(
                "http.response",
                url=self._base_url,
                status=response.status_code,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                bytes=len(response.content),
            )
            self._raise_for_status(response)
            try:
                body = response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                _log.error(
                    "http.body_not_json",
                    url=self._base_url,
                    body=response.text,
                    message=str(exc),
                )
                raise ModelResponseParseError(
                    f"provider 响应不是合法 JSON: {exc}"
                ) from exc
            _log.debug("http.response.body", body=body)
        return self._parse_completion(body)

    # ---- 流式（§9）----

    def stream(self, request: ProviderRequest) -> Iterator[ProviderStreamChunk]:
        self._raise_if_cancelled(request)
        payload = self._payload(request, stream=True)
        _log.debug("http.stream.request", url=self._base_url, payload=payload)
        started = time.perf_counter()
        chunks = 0
        first_chunk_ms = 0.0
        client = self._client_factory()
        try:
            with client.stream(
                "POST",
                self._base_url,
                json=payload,
                headers=self._headers(request),
                timeout=request.timeout_seconds,
            ) as response:
                self._raise_for_stream_status(response)
                _log.info(
                    "http.stream.open",
                    url=self._base_url,
                    status=response.status_code,
                    elapsed_ms=(time.perf_counter() - started) * 1000.0,
                )
                for line in response.iter_lines():
                    # 取消：读行间隙检查并中止底层连接，不等待自然超时（§9）。
                    if self._cancelled(request):
                        response.close()
                        _log.info("http.stream.cancelled", chunks=chunks)
                        raise ModelCancelledError("流式调用已被取消，连接已中止")
                    chunk = self._parse_sse_line(line)
                    if chunk is not None:
                        chunks += 1
                        if chunks == 1:
                            first_chunk_ms = (time.perf_counter() - started) * 1000.0
                        yield chunk
        except httpx.TimeoutException as exc:
            _log.warning(
                "http.stream.timeout",
                url=self._base_url,
                chunks=chunks,
                timeout_seconds=request.timeout_seconds,
                message=str(exc),
            )
            raise ModelTimeoutError(f"provider 流式请求超时: {exc}") from exc
        except httpx.TransportError as exc:
            _log.warning(
                "http.stream.transport_error",
                url=self._base_url,
                chunks=chunks,
                error=type(exc).__name__,
                message=str(exc),
            )
            raise ModelUnavailableError(f"provider 流式连接失败: {exc}") from exc
        finally:
            client.close()
            _log.info(
                "http.stream.closed",
                url=self._base_url,
                chunks=chunks,
                # 首块延迟是"模型卡在哪"最直接的读数: 首块慢是排队或者在思考,
                # 首块快而总时长长是在正常出字.
                first_chunk_ms=first_chunk_ms,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
            )

    # ---- 请求映射 ----

    def _headers(self, request: ProviderRequest) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if request.credential is not None:
            headers["Authorization"] = f"Bearer {request.credential.value}"
        return headers

    def _payload(self, request: ProviderRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": self._messages(request),
        }
        params = request.params
        if params.temperature is not None:
            payload["temperature"] = params.temperature
        if params.top_p is not None:
            payload["top_p"] = params.top_p
        if params.max_output_tokens is not None:
            payload["max_tokens"] = params.max_output_tokens
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": dict(tool.parameters),
                    },
                }
                for tool in request.tools
            ]
        payload.update(self._response_format(request))
        payload.update(self._thinking_fields(request))
        # provider 私有选项：按命名空间整体透传，只取本 provider 的段（§3.5）。
        options = params.provider_options.get(self._provider_id)
        if options:
            payload.update(dict(options))
        if stream:
            payload["stream"] = True
        return payload

    def _messages(self, request: ProviderRequest) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        for message in request.messages:
            messages.extend(self._convert_message(message))
        return messages

    def _convert_message(self, message: ChatMessage) -> list[dict[str, Any]]:
        # tool 结果消息（§10）：每个 ToolResultBlock 翻译成一条 role=tool 消息，
        # 通过 tool_call_id 与触发它的 tool call 关联。
        if message.role.value == "tool":
            return [
                {
                    "role": "tool",
                    "tool_call_id": block.tool_call_id,
                    "content": block.content,
                }
                for block in message.content
                if isinstance(block, ToolResultBlock)
            ]
        text = "".join(
            block.text for block in message.content if isinstance(block, TextBlock)
        )
        converted: dict[str, Any] = {"role": message.role.value, "content": text}
        if message.tool_calls:
            converted["tool_calls"] = [
                {
                    "id": call.tool_call_id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(
                            dict(call.arguments), ensure_ascii=False
                        ),
                    },
                }
                for call in message.tool_calls
            ]
            if not text:
                converted["content"] = None
        return [converted]

    def _response_format(self, request: ProviderRequest) -> dict[str, Any]:
        # native structured output（§3.7）：schema 映射为 json_schema response_format。
        if request.response_schema is not None:
            return {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": request.schema_name or "structured_output",
                        "schema": dict(request.response_schema),
                        "strict": request.strict_schema,
                    },
                }
            }
        if request.params.response_format == "json_object":
            return {"response_format": {"type": "json_object"}}
        return {}

    def _thinking_fields(
        self,
        request: ProviderRequest,
    ) -> dict[str, Any]:
        """将模型级 thinking 设置翻译为供应商协议字段。
        thinking.enabled == true：发送 thinking.type=enabled
        thinking.enabled == false：发送 thinking.type=disabled
        开启且有 effort 时, 再发送 reasoning_effort, 关闭时不会发送 effort
        ThinkingDialect.NONE 仍不发送 Thinking 字段
        开启但没有 effort 时，也会发送 thinking.type=enabled, 此时为模型默认强度
        """
        thinking = request.thinking

        if thinking is None or self._thinking_dialect is ThinkingDialect.NONE:
            return {}

        fields: dict[str, Any] = {
            "thinking": {
                "type": "enabled" if thinking.enabled else "disabled",
            }
        }
        if not thinking.enabled:
            return fields

        effort = thinking.effort
        if effort is None:
            return fields

        if self._thinking_dialect is ThinkingDialect.EFFORT:
            fields["reasoning_effort"] = effort.value
            return fields

        return fields

    # ---- 响应映射 ----

    def _parse_completion(self, body: Mapping[str, Any]) -> ProviderResponse:
        try:
            choice = body["choices"][0]
            message = choice.get("message") or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelResponseParseError(
                f"provider 响应缺少 choices/message 结构: {exc!r}"
            ) from exc
        content = message.get("content") or ""
        finish = _FINISH_REASONS.get(
            str(choice.get("finish_reason") or "stop"), FinishReason.STOP
        )
        tool_calls = self._parse_tool_calls(message.get("tool_calls") or [])
        usage = self._parse_usage(body.get("usage"))
        raw_metadata: dict[str, str] = {}
        if body.get("id"):
            raw_metadata["provider_request_id"] = str(body["id"])
        if usage is not None and usage.cached_input_tokens > 0:
            raw_metadata["cache_hit"] = "true"
        return ProviderResponse(
            content=str(content),
            finish_reason=finish,
            tool_calls=tool_calls,
            usage=usage,
            raw_metadata=raw_metadata,
        )

    def _parse_tool_calls(self, raw_calls: list[Any]) -> tuple[ToolCall, ...]:
        calls: list[ToolCall] = []
        for raw in raw_calls:
            function = raw.get("function") or {}
            raw_arguments = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                raise ModelResponseParseError(
                    f"工具调用参数不是合法 JSON: {exc}"
                ) from exc
            if not isinstance(arguments, dict):
                raise ModelResponseParseError("工具调用参数必须是 JSON 对象")
            calls.append(
                ToolCall(
                    tool_call_id=str(raw.get("id") or f"tool_call_{len(calls)}"),
                    name=str(function.get("name") or ""),
                    arguments=arguments,
                )
            )
        return tuple(calls)

    def _parse_usage(self, raw: Mapping[str, Any] | None) -> ModelUsage | None:
        if not raw:
            return None
        prompt_details = raw.get("prompt_tokens_details") or {}
        completion_details = raw.get("completion_tokens_details") or {}
        return ModelUsage(
            input_tokens=int(raw.get("prompt_tokens") or 0),
            output_tokens=int(raw.get("completion_tokens") or 0),
            cached_input_tokens=int(prompt_details.get("cached_tokens") or 0),
            reasoning_tokens=int(completion_details.get("reasoning_tokens") or 0),
            total_tokens=int(raw.get("total_tokens") or 0),
        )

    def _parse_sse_line(self, line: str) -> ProviderStreamChunk | None:
        text = line.strip()
        if not text.startswith("data:"):
            return None
        data = text[len("data:") :].strip()
        if not data or data == "[DONE]":
            return None
        try:
            body = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ModelResponseParseError(f"SSE chunk 不是合法 JSON: {exc}") from exc
        choices = body.get("choices") or []
        delta: Mapping[str, Any] = {}
        finish: FinishReason | None = None
        if choices:
            choice = choices[0]
            delta = choice.get("delta") or {}
            raw_finish = choice.get("finish_reason")
            if raw_finish:
                finish = _FINISH_REASONS.get(str(raw_finish), FinishReason.STOP)
        return ProviderStreamChunk(
            delta_text=delta.get("content") or None,
            tool_call_deltas=self._parse_tool_call_deltas(
                delta.get("tool_calls") or []
            ),
            usage=self._parse_usage(body.get("usage")),
            finish_reason=finish,
        )

    @staticmethod
    def _parse_tool_call_deltas(raw_calls: list[Any]) -> tuple[ToolCallDelta, ...]:
        """`delta.tool_calls` 的每一项都要保留.

        模型一次响应里请求多个工具时, 供应商可以把它们的片段塞进同一个 chunk. 只取
        首项会让其余调用连同它们的 index 一起消失, 而累积器是按 index 聚合的 ——
        丢掉的调用不会执行, 模型却以为自己请求过.
        """
        deltas: list[ToolCallDelta] = []
        for position, raw in enumerate(raw_calls):
            function = raw.get("function") or {}
            # index 缺省退回到在数组里的位置, 而不是恒为 0: 后者会让同一 chunk 的多个
            # 调用在累积器里互相覆盖成一个.
            raw_index = raw.get("index")
            deltas.append(
                ToolCallDelta(
                    index=int(raw_index) if raw_index is not None else position,
                    tool_call_id=str(raw["id"]) if raw.get("id") else None,
                    name=str(function["name"]) if function.get("name") else None,
                    arguments_delta=str(function.get("arguments") or ""),
                )
            )
        return tuple(deltas)

    # ---- 错误映射（§12）----

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        self._raise_http_error(response.status_code, response)

    def _raise_for_stream_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        response.read()  # stream 模式下先读完错误体再关闭
        self._raise_http_error(response.status_code, response)

    def _raise_http_error(self, status: int, response: httpx.Response) -> None:
        summary = self._error_summary(response)
        # 错误体**整段**进日志, 不只是 error.message 的前 200 字: 那份摘要是给模型与
        # 用户看的, 而排查一次 400 往往要看 provider 到底指着哪个字段说不合法.
        _log.error(
            "http.error",
            url=self._base_url,
            status=status,
            summary=summary,
            body=response.text,
        )
        if status in (401, 403):
            raise ModelAuthError(f"provider 认证失败（{status}）: {summary}")
        if status == 429:
            raise ModelRateLimitError(
                f"provider 限流（429）: {summary}",
                retry_after=self._retry_after(response),
            )
        if status == 400 and _is_context_overflow(response.text):
            # 与其它 400 分开: 这一条**有救** —— 压一次上下文再发就能过, 而按普通
            # ModelBadRequestError 处理会让一轮跑了几十步的工作整个作废.
            raise ModelContextOverflowError(f"provider 拒绝请求（400）: {summary}")
        if 400 <= status < 500:
            raise ModelBadRequestError(f"provider 拒绝请求（{status}）: {summary}")
        raise ModelProviderInternalError(f"provider 内部错误（{status}）: {summary}")

    @staticmethod
    def _error_summary(response: httpx.Response) -> str:
        """错误体安全摘要：只取 error.message 的前 200 字符，绝不含凭证。"""
        try:
            body = response.json()
            message = body.get("error", {}).get("message", "")
        except (json.JSONDecodeError, ValueError, AttributeError):
            message = ""
        return str(message)[:200] if message else "(无错误详情)"

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        raw = response.headers.get("retry-after")
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    # ---- 取消 ----

    @staticmethod
    def _cancelled(request: ProviderRequest) -> bool:
        return request.cancel_token is not None and request.cancel_token.cancelled

    def _raise_if_cancelled(self, request: ProviderRequest) -> None:
        if self._cancelled(request):
            raise ModelCancelledError("调用已被取消，未发送 provider 请求")


# 各家对"提示词太长"的说法. 判据只能是措辞: OpenAI 兼容协议在 400 上没有约定错误码,
# 而 GLM 回 1261, DeepSeek 回一句话, 本地端点各说各的.
_CONTEXT_OVERFLOW_MARKERS = (
    "context_length_exceeded",
    "context length",
    "maximum context",
    "exceeds max length",
    "prompt is too long",
    "too many tokens",
    "reduce the length",
)


def _is_context_overflow(body: str) -> bool:
    lowered = body.lower()
    return any(marker in lowered for marker in _CONTEXT_OVERFLOW_MARKERS)
