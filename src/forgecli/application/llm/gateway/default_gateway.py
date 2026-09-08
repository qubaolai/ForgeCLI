"""统一 LLM 网关实现 DefaultLlmGateway（ADR-0011 §3.1 / §8 / §17，ADR-0012）。

complete / stream / complete_structured 共用同一条治理管线，按 §8 顺序执行：

    取消预检 -> 选择解析（resolver + catalog 校验）-> thinking/参数合并（§3.5 / §5）
    -> 响应缓存查（§14）-> 健康熔断检（§12.1）
    -> token 估算 + 上下文窗口预检（§11.4）-> 预算裁决（§11.3）
    -> 凭证获取 + 有界重试环（§7 / §12）-> provider 调用 -> 归一化。

不含客户端主动限流（曾属 §13）：单用户单 key 的个人 CLI 场景下，本地按配置猜测
的限流阈值没有信息优势，交互 origin 上的快速失败只会拒绝掉 provider 本可能接受
的请求；429 由已有的短等重试环（§2）与凭证冷却处理，见 governance.py 模块说明。

重试规则（§12 / ADR-0012 §2）：流式与非流式共用同一凭证级重试环——auth / 429
依次尝试同 provider 的其他 credential；timeout / 连接失败按同凭证有限重试；
429 带 retry_after 且 <= wait_threshold_seconds 时经注入 sleeper 本地等待后
**同一凭证**重试（wait_retries）。stream 仅在**首个 provider chunk 产出之前**
允许重试，首包之后的错误一律按中断收尾（§9），不重试、不重放。总次数受
provider max_retries 约束；任何重试**不**切换 provider/model。重试摘要写入
raw_metadata（credential_retries / transport_retries / wait_retries）。

凭证不被独占（§7）：`CredentialPool.get_credential` 直接返回凭证，不做租借 /
占用，多个并发调用可拿到同一凭证的值；网关不对凭证做并发限制。凭证可用性只在
mark_failed（401/429 冷却）或引用本身消失（配置/解析层面）时变化。

边界：网关不直接写 events / state / usage 文件；usage 随 ModelResponse 返回，
由 AgentTurnService 经 UsageMeter 转草稿后落盘（§11.1）。resolver 为必备协作件
（ADR-0012 §10：06-30 的无 resolver 兼容路径与 validate_model 参数已清理）。

**这个模块负责编排**，几件自成一套判据的事各自成模块（ADR-0048 决策 5）：

- ``retry.CredentialRetryLoop`` 错误分类与凭证轮换。complete 与 stream 首包前
  **共用同一份**——它原先是两份一模一样的抄写。
- ``structured`` schema 指令注入、围栏剥离与校验（纯函数）。
- ``cache`` / ``governance`` / ``streaming`` / ``token_estimator`` 各自的老本行。

网关仍是统一调用入口：三个公开方法都从这里进，治理管线的顺序也只写在这里一处。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping

from forgecli.application.llm.gateway import structured
from forgecli.application.llm.gateway.cache import (
    InMemoryResponseCache,
    structured_schema_digest,
)
from forgecli.application.llm.gateway.credentials import CredentialPool
from forgecli.application.llm.gateway.errors import (
    ModelBadRequestError,
    ModelCancelledError,
    ModelCapabilityError,
    ModelContextOverflowError,
    ModelGatewayError,
    ModelResponseParseError,
    cancelled,
    raise_if_cancelled,
    with_context,
)
from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.application.llm.gateway.governance import SlidingWindowHealthRegistry
from forgecli.application.llm.gateway.provider import (
    ModelProvider,
    ProviderRequest,
    ProviderResponse,
)
from forgecli.application.llm.gateway.provider_registry import ProviderRegistry
from forgecli.application.llm.gateway.provider_settings import (
    ProviderRuntimeSettings,
    ProviderSettingsSource,
)
from forgecli.application.llm.gateway.retry import (
    CredentialRetryLoop,
    RetryCounts,
)
from forgecli.application.llm.gateway.token_estimator import (
    ApproximateTokenEstimator,
)
from forgecli.application.llm.selection import (
    ConfigBackedSelectionResolver,
)
from forgecli.domain.model.catalog import ModelCatalogEntry
from forgecli.domain.model.credentials import Credential
from forgecli.domain.model.model_ref import ModelRef
from forgecli.domain.model.origin import benefits_from_thinking
from forgecli.domain.model.params import (
    ModelParams,
    ThinkingConfig,
)
from forgecli.domain.model.request import ModelRequest, StructuredModelRequest
from forgecli.domain.model.response import (
    FinishReason,
    ModelResponse,
    ModelUsage,
    StructuredModelResponse,
)
from forgecli.domain.model.streaming import ModelStreamChunk, ProviderStreamChunk
from forgecli.domain.model.thinking import ThinkingMode
from forgecli.shared.observability.log import get_log

_log = get_log(__name__)

# settings_source 未注入时的 provider 默认（与配置切片默认一致）。
_FALLBACK_TIMEOUT_SECONDS = 60.0
_FALLBACK_MAX_RETRIES = 2


class DefaultLlmGateway(LlmGateway):
    """网关实现：解析选择、治理管线、路由 adapter、归一化响应。"""

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        resolver: ConfigBackedSelectionResolver,
        timer: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        token_estimator: ApproximateTokenEstimator | None = None,
        settings_source: ProviderSettingsSource | None = None,
        credential_pool: CredentialPool | None = None,
        health_registry: SlidingWindowHealthRegistry | None = None,
        cache: InMemoryResponseCache | None = None,
        structured_retry_limit: int = 1,
    ) -> None:
        self._registry = registry
        # 必备协作件（ADR-0012 §10）：current/explicit 都由 resolver 解析并经
        # catalog 校验，无 resolver 兼容路径已清理。
        self._resolver = resolver
        # 注入计时器：让 latency_ms 在测试中可钉死。
        self._timer = timer
        # 注入 sleeper（ADR-0012 §2）：429 短等重试经它完成，测试钉死不真实 sleep。
        self._estimator = token_estimator or ApproximateTokenEstimator()
        # 精确分词器接入点（ADR-0012 §6）：按已解析 ref 选估算器，无映射回落近似。
        self._settings_source = settings_source
        self._health = health_registry or SlidingWindowHealthRegistry(enabled=False)
        # 两条主路径 (complete / stream 首包前) 共用它 —— 那张错误分类表只该有一份。
        self._retry = CredentialRetryLoop(
            credential_pool, self._health, sleeper=sleeper
        )
        self._cache = cache or InMemoryResponseCache()
        self._structured_retry_limit = structured_retry_limit

    # ---- LlmGateway 端口 ----

    def complete(self, request: ModelRequest) -> ModelResponse:
        ref, entry = self._resolve_selection(request)
        thinking = self._resolve_thinking(request, ref, entry)
        self._check_tool_capability(request, ref, entry)
        _log.info(
            "llm.complete",
            provider=ref.provider,
            model=ref.model,
            origin=request.origin.value,
            messages=len(request.messages),
            tools=len(request.tools),
            thinking=thinking.enabled,
            thinking_effort=None if thinking.effort is None else thinking.effort.value,
            context_window=entry.context_window,
        )
        return self._complete_resolved(
            request, ref, entry, request.params, thinking, use_cache=True
        )

    def complete_structured(
        self, request: StructuredModelRequest
    ) -> StructuredModelResponse:
        base = request.model_request
        ref, entry = self._resolve_selection(base)
        thinking = self._resolve_thinking(base, ref, entry)
        self._check_tool_capability(base, ref, entry)
        capabilities = self._registry.get(ref.provider).capabilities()
        if capabilities.supports_structured_output and entry.supports_structured_output:
            response, data, errors = self._structured_native(
                base, ref, entry, base.params, thinking, request
            )
        else:
            # 受控解析降级（§3.7）：schema 指令注入 prompt，有上限重试，不换模型。
            response, data, errors = self._structured_degraded(
                base, ref, entry, base.params, thinking, request
            )
        if errors and request.strict:
            raise ModelResponseParseError(
                f"结构化输出未通过 schema {request.schema_name!r} 校验: "
                + "; ".join(errors),
                provider=ref.provider,
                model=ref.model,
                request_id=base.request_id,
            )
        return StructuredModelResponse(
            request_id=base.request_id,
            provider=ref.provider,
            model=ref.model,
            data=data,
            usage=response.usage,
            latency_ms=response.latency_ms,
            validation_errors=tuple(errors),
            raw_metadata=response.raw_metadata,
        )

    def stream(self, request: ModelRequest) -> Iterator[ModelStreamChunk]:
        ref, entry = self._resolve_selection(request)
        thinking = self._resolve_thinking(request, ref, entry)
        self._check_tool_capability(request, ref, entry)
        params = request.params
        provider = self._registry.get(ref.provider)
        if not provider.capabilities().supports_streaming:
            raise ModelBadRequestError(
                f"provider {ref.provider!r} 的 adapter 不支持 streaming",
                provider=ref.provider,
                model=ref.model,
                request_id=request.request_id,
            )
        estimated_input = self._pre_call_checks(request, ref, entry, params)
        settings = self._settings(ref.provider)
        raise_if_cancelled(request, ref)
        _log.info(
            "llm.stream",
            provider=ref.provider,
            model=ref.model,
            origin=request.origin.value,
            messages=len(request.messages),
            tools=len(request.tools),
            thinking=thinking.enabled,
            thinking_effort=None if thinking.effort is None else thinking.effort.value,
            estimated_input_tokens=estimated_input,
            timeout_seconds=settings.timeout_seconds,
            max_retries=settings.max_retries,
        )
        return self._stream_with_retries(
            request,
            ref,
            entry,
            provider,
            params,
            thinking,
            settings,
            estimated_input,
        )

    # ---- 非流式主路径 ----

    def _complete_resolved(
        self,
        request: ModelRequest,
        ref: ModelRef,
        entry: ModelCatalogEntry,
        params: ModelParams,
        thinking: ThinkingConfig,
        *,
        use_cache: bool,
        cache_digest: str | None = None,
        response_schema: Mapping[str, object] | None = None,
        schema_name: str | None = None,
        strict_schema: bool = True,
    ) -> ModelResponse:
        raise_if_cancelled(request, ref)
        if use_cache:
            cached = self._cache.lookup(
                request, ref, thinking=thinking, schema_digest=cache_digest
            )
            if cached is not None:
                _log.info("llm.cache_hit", provider=ref.provider, model=ref.model)
                return cached
        counts = RetryCounts()
        start = self._timer()
        try:
            estimated_input = self._pre_call_checks(request, ref, entry, params)
            settings = self._settings(ref.provider)
            provider = self._registry.get(ref.provider)
            provider_response = self._invoke_with_retries(
                provider,
                request,
                ref,
                entry,
                params,
                thinking,
                settings,
                counts,
                response_schema=response_schema,
                schema_name=schema_name,
                strict_schema=strict_schema,
            )
        except ModelGatewayError as exc:
            _log.error(
                "llm.failed",
                provider=ref.provider,
                model=ref.model,
                error=type(exc).__name__,
                message=str(exc),
                elapsed_ms=(self._timer() - start) * 1000.0,
                **counts.summary(),
            )
            raise
        latency_ms = (self._timer() - start) * 1000.0

        usage = provider_response.usage or self._estimate_usage(
            ref, estimated_input, provider_response.content
        )
        raw_metadata: dict[str, str] = dict(provider_response.raw_metadata)
        raw_metadata.update(counts.summary())
        response = ModelResponse(
            request_id=request.request_id,
            provider=ref.provider,
            model=ref.model,
            content=provider_response.content,
            finish_reason=provider_response.finish_reason,
            usage=usage,
            latency_ms=latency_ms,
            tool_calls=provider_response.tool_calls,
            raw_metadata=raw_metadata,
        )
        _log.info(
            "llm.completed",
            provider=ref.provider,
            model=ref.model,
            finish_reason=response.finish_reason.value,
            latency_ms=latency_ms,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            estimated=usage.estimated,
            content_chars=len(response.content),
            tool_calls=[call.name for call in response.tool_calls],
            **counts.summary(),
        )
        self._health.record_success(ref)
        if use_cache:
            self._cache.store(
                request,
                ref,
                response,
                thinking=thinking,
                schema_digest=cache_digest,
            )
        return response

    def _invoke_with_retries(
        self,
        provider: ModelProvider,
        request: ModelRequest,
        ref: ModelRef,
        entry: ModelCatalogEntry,
        params: ModelParams,
        thinking: ThinkingConfig,
        settings: ProviderRuntimeSettings,
        counts: RetryCounts,
        *,
        response_schema: Mapping[str, object] | None,
        schema_name: str | None,
        strict_schema: bool,
    ) -> ProviderResponse:
        """一次非流式 provider 调用, 错误分类与重试见 retry.CredentialRetryLoop。"""

        def attempt(credential: Credential | None) -> ProviderResponse:
            return provider.complete(
                self._provider_request(
                    request,
                    ref,
                    entry,
                    params,
                    thinking,
                    settings,
                    credential,
                    response_schema=response_schema,
                    schema_name=schema_name,
                    strict_schema=strict_schema,
                )
            )

        return self._retry.run(
            attempt, request=request, ref=ref, settings=settings, counts=counts
        )

    # ---- 流式主路径（ADR-0012 §2：首包前共用重试环）----

    def _stream_with_retries(
        self,
        request: ModelRequest,
        ref: ModelRef,
        entry: ModelCatalogEntry,
        provider: ModelProvider,
        params: ModelParams,
        thinking: ThinkingConfig,
        settings: ProviderRuntimeSettings,
        estimated_input: int,
    ) -> Iterator[ModelStreamChunk]:
        counts = RetryCounts()
        start = self._timer()
        try:
            provider_iter, first_chunk, credential = self._open_stream(
                request, ref, entry, provider, params, thinking, settings, counts
            )
        except ModelCancelledError:
            # 首包前取消：按 §9 产出中断收尾块，不抛给渲染层。
            yield self._interrupt_chunk(request, ref, 0, estimated_input, [])
            return
        except ModelGatewayError:
            raise
        yield from self._stream_body(
            request,
            ref,
            provider_iter,
            first_chunk,
            estimated_input,
            credential,
            counts,
            start,
        )

    def _open_stream(
        self,
        request: ModelRequest,
        ref: ModelRef,
        entry: ModelCatalogEntry,
        provider: ModelProvider,
        params: ModelParams,
        thinking: ThinkingConfig,
        settings: ProviderRuntimeSettings,
        counts: RetryCounts,
    ) -> tuple[
        Iterator[ProviderStreamChunk],
        ProviderStreamChunk | None,
        Credential | None,
    ]:
        """首包前的凭证级重试环：与 complete 同一份分类表；拿到首个 chunk 即返回。

        ``mark_success=False``: 首包不等于这条流会走完, 凭证的成功在 ``_stream_body``
        的收尾处记 —— 首包之后中断的流不该把一把有问题的凭证标成好用。
        """

        def attempt(
            credential: Credential | None,
        ) -> tuple[
            Iterator[ProviderStreamChunk],
            ProviderStreamChunk | None,
            Credential | None,
        ]:
            provider_iter = provider.stream(
                self._provider_request(
                    request, ref, entry, params, thinking, settings, credential
                )
            )
            return provider_iter, next(provider_iter, None), credential

        return self._retry.run(
            attempt,
            request=request,
            ref=ref,
            settings=settings,
            counts=counts,
            what="流式调用",
            mark_success=False,
        )

    def _stream_body(
        self,
        request: ModelRequest,
        ref: ModelRef,
        provider_iter: Iterator[ProviderStreamChunk],
        first_chunk: ProviderStreamChunk | None,
        estimated_input: int,
        credential: Credential | None,
        counts: RetryCounts,
        start: float,
    ) -> Iterator[ModelStreamChunk]:
        """首包之后：归一化 chunk；错误 / 取消只做中断收尾（§9），不重试。"""
        sequence = 0
        received_text: list[str] = []
        last_usage: ModelUsage | None = None
        last_finish: FinishReason | None = None

        def normalize(provider_chunk: ProviderStreamChunk) -> ModelStreamChunk:
            nonlocal last_usage, last_finish
            if provider_chunk.delta_text:
                received_text.append(provider_chunk.delta_text)
            if provider_chunk.usage is not None:
                last_usage = provider_chunk.usage
            if provider_chunk.finish_reason is not None:
                last_finish = provider_chunk.finish_reason
            return ModelStreamChunk(
                request_id=request.request_id,
                sequence=sequence,
                provider=ref.provider,
                model=ref.model,
                delta_text=provider_chunk.delta_text,
                tool_call_deltas=provider_chunk.tool_call_deltas,
                usage_delta=provider_chunk.usage,
                finish_reason=provider_chunk.finish_reason,
            )

        if first_chunk is not None:
            yield normalize(first_chunk)
            sequence += 1
        while True:
            if cancelled(request):
                _close_iterator(provider_iter)
                yield self._interrupt_chunk(
                    request, ref, sequence, estimated_input, received_text
                )
                return
            try:
                provider_chunk = next(provider_iter)
            except StopIteration:
                break
            except ModelCancelledError:
                yield self._interrupt_chunk(
                    request, ref, sequence, estimated_input, received_text
                )
                return
            except ModelGatewayError as exc:
                # 首包之后（ADR-0012 §2）：不重试、不重放，按中断收尾。
                self._health.record_failure(ref)
                with_context(exc, ref, request)
                yield self._error_chunk(
                    request, ref, sequence, estimated_input, received_text
                )
                return
            except Exception:
                self._health.record_failure(ref)
                yield self._error_chunk(
                    request, ref, sequence, estimated_input, received_text
                )
                return
            yield normalize(provider_chunk)
            sequence += 1
        # 收尾（§9）：最后一块必须补全 usage / finish_reason 汇总。
        usage = last_usage or self._estimate_usage(
            ref, estimated_input, "".join(received_text)
        )
        yield ModelStreamChunk(
            request_id=request.request_id,
            sequence=sequence,
            provider=ref.provider,
            model=ref.model,
            usage_delta=usage,
            finish_reason=last_finish or FinishReason.STOP,
        )
        self._retry.mark_succeeded(credential)
        self._health.record_success(ref)

    def _interrupt_chunk(
        self,
        request: ModelRequest,
        ref: ModelRef,
        sequence: int,
        estimated_input: int,
        received_text: list[str],
    ) -> ModelStreamChunk:
        """取消 / 中断收尾块（§9）：partial usage 估算 + user_cancelled。"""
        usage = self._estimate_usage(ref, estimated_input, "".join(received_text))
        return ModelStreamChunk(
            request_id=request.request_id,
            sequence=sequence,
            provider=ref.provider,
            model=ref.model,
            usage_delta=usage,
            finish_reason=FinishReason.USER_CANCELLED,
            interrupted=True,
        )

    def _error_chunk(
        self,
        request: ModelRequest,
        ref: ModelRef,
        sequence: int,
        estimated_input: int,
        received_text: list[str],
    ) -> ModelStreamChunk:
        """首包后 provider 错误的中断收尾块（ADR-0012 §2）：finish=error。"""
        usage = self._estimate_usage(ref, estimated_input, "".join(received_text))
        return ModelStreamChunk(
            request_id=request.request_id,
            sequence=sequence,
            provider=ref.provider,
            model=ref.model,
            usage_delta=usage,
            finish_reason=FinishReason.ERROR,
            interrupted=True,
        )

    # ---- 结构化输出 ----

    def _structured_native(
        self,
        base: ModelRequest,
        ref: ModelRef,
        entry: ModelCatalogEntry,
        params: ModelParams,
        thinking: ThinkingConfig,
        request: StructuredModelRequest,
    ) -> tuple[ModelResponse, object | None, list[str]]:
        """native 通道（§3.7）：schema 摘要并入响应缓存指纹（ADR-0012 §3）。"""
        digest = structured_schema_digest(request.schema_name, request.schema)
        cached = self._cache.lookup(base, ref, thinking=thinking, schema_digest=digest)
        if cached is not None:
            data, errors = structured.parse(cached.content, request)
            if not errors:
                return cached, data, errors
        response = self._complete_resolved(
            base,
            ref,
            entry,
            params,
            thinking,
            use_cache=False,
            response_schema=request.schema,
            schema_name=request.schema_name,
            strict_schema=request.strict,
        )
        data, errors = structured.parse(response.content, request)
        if not errors:
            # 只缓存通过 schema 校验的响应，避免缓存放大一次坏输出。
            self._cache.store(
                base,
                ref,
                response,
                thinking=thinking,
                schema_digest=digest,
            )
        return response, data, errors

    def _structured_degraded(
        self,
        base: ModelRequest,
        ref: ModelRef,
        entry: ModelCatalogEntry,
        params: ModelParams,
        thinking: ThinkingConfig,
        request: StructuredModelRequest,
    ) -> tuple[ModelResponse, object | None, list[str]]:
        prompted = structured.with_instruction(base, request)
        cached = self._cache.lookup(prompted, ref, thinking=thinking)
        if cached is not None:
            cached_data, cached_errors = structured.parse(cached.content, request)
            if not cached_errors:
                return cached, cached_data, cached_errors
        response: ModelResponse | None = None
        data: object | None = None
        errors: list[str] = []
        for _attempt in range(self._structured_retry_limit + 1):
            response = self._complete_resolved(
                prompted, ref, entry, params, thinking, use_cache=False
            )
            data, errors = structured.parse(response.content, request)
            if not errors:
                self._cache.store(prompted, ref, response, thinking=thinking)
                break
        assert response is not None
        return response, data, errors

    # ---- 选择解析与参数合并 ----

    def _resolve_selection(
        self, request: ModelRequest
    ) -> tuple[ModelRef, ModelCatalogEntry]:
        """把选择解析成 (ModelRef, 目录条目)；resolver 为必备协作件（ADR-0012 §10）。"""
        resolved = self._resolver.resolve(origin=request.origin)
        return resolved.ref, resolved.entry

    def _resolve_thinking(
        self,
        request: ModelRequest,
        ref: ModelRef,
        entry: ModelCatalogEntry,
    ) -> ThinkingConfig:
        """读取模型配置与调用用途, 生成 provider 运行时参数。

        用途参与判断而不只看模型配置: 起标题, 压缩上下文与做摘要都是把已经在上下文里的
        东西换个形状, 推理预算花在那里是纯浪费 —— 而它们与真正要推理的调用共用同一个
        模型, 只按模型配就只能一起开或一起关 (ADR-0011 §3.3 明写 origin 用于参数默认)。

        这个参数以前收了但没用, 于是 `compact` 与 `act` 拿的是同一份思考预算。
        """
        if not benefits_from_thinking(request.origin):
            return ThinkingConfig(enabled=False, effort=None)
        enabled = entry.thinking_mode is ThinkingMode.ON
        return ThinkingConfig(
            enabled=enabled,
            effort=(entry.effective_thinking_effort if enabled else None),
        )

    def _check_tool_capability(
        self,
        request: ModelRequest,
        ref: ModelRef,
        entry: ModelCatalogEntry,
    ) -> None:
        if not request.tools:
            return
        capabilities = self._registry.get(ref.provider).capabilities()
        if not capabilities.supports_tools:
            message = "当前供应商不支持工具调用"
        elif not entry.supports_tool_calling:
            message = "当前模型未声明支持工具调用"
        else:
            return
        raise ModelCapabilityError(
            message,
            provider=ref.provider,
            model=ref.model,
            request_id=request.request_id,
        )

    # ---- 治理管线 ----

    def _pre_call_checks(
        self,
        request: ModelRequest,
        ref: ModelRef,
        entry: ModelCatalogEntry,
        params: ModelParams,
    ) -> int:
        """健康 / token 估算 / 窗口预检 / 预算裁决；返回估算输入 token。"""
        self._health.check(ref)
        estimated_input = self._estimator_for(ref).estimate_input(
            messages=request.messages,
            system_prompt=request.system_prompt,
            tools=request.tools,
        )
        expected_output = params.max_output_tokens or entry.max_output_tokens or 0
        if estimated_input + expected_output > entry.context_window:
            _log.error(
                "llm.context_overflow",
                provider=ref.provider,
                model=ref.model,
                estimated_input=estimated_input,
                expected_output=expected_output,
                context_window=entry.context_window,
            )
            raise ModelContextOverflowError(
                f"估算输入 {estimated_input} + 预期输出 {expected_output} "
                f"超出模型 {ref} 上下文窗口 {entry.context_window}",
                provider=ref.provider,
                model=ref.model,
                request_id=request.request_id,
            )
        return estimated_input

    def _settings(self, provider_id: str) -> ProviderRuntimeSettings:
        if self._settings_source is None:
            return ProviderRuntimeSettings(
                provider_id=provider_id,
                timeout_seconds=_FALLBACK_TIMEOUT_SECONDS,
                max_retries=_FALLBACK_MAX_RETRIES,
                credential_refs=(),
            )
        return self._settings_source.settings_for(provider_id)

    def _provider_request(
        self,
        request: ModelRequest,
        ref: ModelRef,
        entry: ModelCatalogEntry,
        params: ModelParams,
        thinking: ThinkingConfig,
        settings: ProviderRuntimeSettings,
        credential: Credential | None,
        *,
        response_schema: Mapping[str, object] | None = None,
        schema_name: str | None = None,
        strict_schema: bool = True,
    ) -> ProviderRequest:
        # 超时合并（§5）：请求级 > provider 默认。
        timeout = settings.timeout_seconds
        return ProviderRequest(
            model=ref.model,
            messages=request.messages,
            params=params,
            thinking=thinking,
            system_prompt=request.system_prompt,
            tools=request.tools,
            timeout_seconds=timeout,
            cancel_token=request.cancel_token,
            credential=credential,
            response_schema=response_schema,
            schema_name=schema_name,
            strict_schema=strict_schema,
        )

    # ---- 小工具 ----

    def _estimator_for(self, ref: ModelRef) -> ApproximateTokenEstimator:
        """取估算器。

        ADR-0028：这里曾经先查一个 TokenizerRegistry（按 provider/model 前缀注册精确
        分词器），而全库唯一的构造点传的是一个空 registry，没有任何注册方——每次调用
        都查一张永远为空的表。需要精确分词器时按新 ADR 加回接入点。
        """
        return self._estimator

    def _estimate_usage(
        self, ref: ModelRef, estimated_input: int, content: str
    ) -> ModelUsage:
        """供应商未返回 usage 时按估算器算出并标记 estimated（§3.8）。"""
        output = self._estimator_for(ref).estimate_text(content)
        return ModelUsage(
            input_tokens=estimated_input,
            output_tokens=output,
            total_tokens=estimated_input + output,
            estimated=True,
        )


def _close_iterator(iterator: Iterator[object]) -> None:
    close = getattr(iterator, "close", None)
    if callable(close):
        close()
