"""缓存控制 LlmCacheController（ADR-0011 §14 / ADR-0012 §3）。

缓存分两层，都由 gateway 统一管理，Agent 层无需感知：

Prompt 缓存（降低重复前缀成本）：
    请求侧用 `CacheHint` 标注可缓存前缀（system prompt / 稳定工具定义），provider
    adapter 翻译成各供应商机制；OpenAI-compatible 自动前缀缓存下「静默忽略 +
    归一化命中信息」即为其正确处理（ADR-0012 §4），不报错。命中信息由 adapter
    归一化到 ModelUsage.cached_input_tokens 与 raw_metadata["cache_hit"]。

响应缓存（可选，默认关闭）：
    对确定性、幂等的内部调用按归一化请求指纹缓存响应。指纹排除易变字段
    （request_id / session / turn / 时间戳 / metadata），可选并入 schema 摘要
    （native 结构化输出，ADR-0012 §3）。命中返回 cached=True、真实 usage 记 0。
    主 Agent 对话（chat / act）**硬排除**，不受配置影响。缓存只存在于运行时，
    不写事件日志；TTL 惰性过期，容量满时按 LRU 淘汰最久未命中项。
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
import hashlib
import json
import time

from forgecli.application.llm.gateway.messages import ChatMessage, TextBlock, ToolResultBlock
from forgecli.application.llm.gateway.origin import RequestOrigin
from forgecli.application.llm.gateway.request import ModelRequest
from forgecli.application.llm.gateway.response import ModelResponse, ModelUsage
from forgecli.application.llm.model_ref import ModelRef

# 响应缓存永远不适用的 origin（§14：主 Agent 对话不走响应缓存）。
_NEVER_CACHED_ORIGINS = frozenset({RequestOrigin.CHAT, RequestOrigin.ACT})


class LlmCacheController(ABC):
    """gateway 内的缓存控制端口。实现不得写事件 / state / usage 文件。"""

    @abstractmethod
    def lookup(
        self,
        request: ModelRequest,
        ref: ModelRef,
        *,
        schema_digest: str | None = None,
    ) -> ModelResponse | None:
        """响应缓存查询；未命中或不适用返回 None。"""

    @abstractmethod
    def store(
        self,
        request: ModelRequest,
        ref: ModelRef,
        response: ModelResponse,
        *,
        schema_digest: str | None = None,
    ) -> None:
        """写入响应缓存；不适用时应为 no-op。"""


class NoopLlmCacheController(LlmCacheController):
    """默认实现：响应缓存关闭（prompt 缓存标注仍随请求透传给 adapter）。"""

    def lookup(
        self, request: ModelRequest, ref: ModelRef, *, schema_digest: str | None = None
    ) -> ModelResponse | None:
        return None

    def store(
        self,
        request: ModelRequest,
        ref: ModelRef,
        response: ModelResponse,
        *,
        schema_digest: str | None = None,
    ) -> None:
        return None

@dataclass
class _CacheSlot:
    stored_at: float
    response: ModelResponse

class InMemoryResponseCache(LlmCacheController):
    """进程内响应缓存：origin 白名单 + TTL + LRU 容量上限（§14 / ADR-0012 §3）。

    chat / act 无论如何不缓存；白名单之外的 origin 也不缓存。
    命中返回副本：cached=True、真实 usage 记 0（estimated=False——这是确定事实，
    不是估算）、raw_metadata 标注 cache_hit。ttl_seconds=None 表示不过期；
    过期为惰性判定（lookup 时对比注入时钟），容量满时淘汰最久未命中项。
    """

    def __init__(
        self,
        *,
        allowed_origins: Iterable[RequestOrigin] = (),
        ttl_seconds: float | None = None,
        max_entries: int = 256,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds is not None and ttl_seconds <= 0:
            raise ValueError("InMemoryResponseCache.ttl_seconds 必须为正数或 None")
        if max_entries <= 0:
            raise ValueError("InMemoryResponseCache.max_entries 必须为正整数")
        self._allowed = frozenset(allowed_origins) - _NEVER_CACHED_ORIGINS
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, _CacheSlot] = OrderedDict()

    
    def lookup(
        self, 
        request: ModelRequest, 
        ref: ModelRef, 
        *, 
        schema_digest: str | None = None
    ) -> ModelResponse | None:
        if request.origin not in self._allowed:
            return None
        key = _fingerprint(request, ref, schema_digest)
        slot = self._entries.get(key)
        if slot is None:
            return None
        if self._expired(slot):
            del self._entries[key]
            return None
        self._entries.move_to_end(key) # 命中刷新 LRU 顺位
        metadata = dict(slot.response.raw_metadata)
        metadata["cache_hit"] = "true"
        return replace(
            slot.response,
            request_id=request.request_id,
            cached=True,
            usage=ModelUsage(input_tokens=0, output_tokens=0, total_tokens=0),
            latency_ms=0.0,
            raw_metadata=metadata,
        )
    
    def store(
        self,
        request: ModelRequest,
        ref: ModelRef,
        response: ModelResponse,
        *,
        schema_digest: str | None = None,
    ) -> None:
        if request.origin not in self._allowed or response.cached:
            return
        key = _fingerprint(request, ref, schema_digest)
        self._entries[key] = _CacheSlot(stored_at=self._clock(), response=response)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)  # 淘汰最久未命中项

    
    def _expired(self, slot: _CacheSlot) -> bool:
        if self._ttl is None:
            return False
        return self._clock() - slot.stored_at > self._ttl
        


    
    # ---- 内部 ----
def _fingerprint(
    request: ModelRequest, ref: ModelRef, schema_digest: str | None = None
) -> str:
    """归一化请求指纹（§14）：排除 request_id / session / turn / 时间戳 / metadata。

    schema_digest 只在 native 结构化路径出现（ADR-0012 §3），保证与普通 complete
    及不同 schema 之间指纹天然不同，不会串味。
    """
    payload: dict[str, object] = {
        "provider": ref.provider,
        "model": ref.model,
        "origin": request.origin.value,
        "system_prompt": request.system_prompt,
        "messages": [_message_key(message) for message in request.messages],
        "params": _params_key(request),
        "tools": [
            {"name": tool.name, "parameters": _canonical(dict(tool.parameters))}
            for tool in request.tools
        ],
    }
    if schema_digest is not None:
        payload["schema_digest"] = schema_digest
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()

def _message_key(message: ChatMessage) -> dict[str, object]:
    blocks: list[object] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            blocks.append({"text": block.text})
        elif isinstance(block, ToolResultBlock):
            blocks.append(
                {"tool_call_id": block.tool_call_id, "content": block.content}
            )
        else:
            blocks.append({"repr": repr(block)})
    return {
        "role": message.role.value,
        "content": blocks,
        "tool_calls": [
            {
                "id": call.tool_call_id,
                "name": call.name,
                "arguments": _canonical(dict(call.arguments)),
            }
            for call in message.tool_calls
        ],
    }

def _params_key(request: ModelRequest) -> dict[str, object]:
    params = request.params
    thinking = params.thinking
    return {
        "temperature": params.temperature,
        "top_p": params.top_p,
        "max_output_tokens": params.max_output_tokens,
        "stop": list(params.stop),
        "response_format": params.response_format,
        "thinking": None
        if thinking is None
        else {
            "enabled": thinking.enabled.value,
            "effort": thinking.effort.value,
            "budget_tokens": thinking.budget_tokens,
        },
        "provider_options": _canonical(
            {ns: dict(opts) for ns, opts in params.provider_options.items()}
        ),
    }


def _canonical(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=repr)