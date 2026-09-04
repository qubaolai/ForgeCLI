"""按脚本回话的 LlmGateway: 不联网, 不要凭证, 每次调用现读脚本文件.

**为什么要它.** 端到端演一遍工具调用 —— 尤其是 `ask_user` 那种要人回话再继续的 ——
需要一个能被精确指定的模型输出, 而真模型给不出稳定的"第 3 步调 ask_user, 第 4 步收尾".
用它可以把一条链路跑通并肉眼验收, 而不必配 key, 不必花钱, 也不必等网络.

**只在 `--mock-llm` 显式指定时装配.** 装配点是 `build_llm_runtime` 的一个参数, 不是
环境变量, 也不是配置项: 一个能被配置文件悄悄打开的假模型, 迟早会有人在真会话里撞上它,
然后对着一段编出来的回答排查半天.

**provider 与 model 固定为 `mock`.** 于是 usage 记录, 日志与状态栏上都看得出这一轮不是
真模型答的 —— 假装成 deepseek 会让事后翻日志的人得到一个错误的事实.

## 脚本格式

一个 JSON 文件, 顶层是数组, 或者带 `responses` 数组的对象 (多余的键一律忽略, 所以可以
塞 `_readme` 之类的说明):

```json
[
  {"text": "我先看看目录结构."},
  {"tool_calls": [{"name": "fs_find", "arguments": {"pattern": "*.py"}}]},
  {"text": "找到了.", "tool_calls": [
      {"name": "ask_user", "arguments": {"question": "用哪个?"}}]},
  {"text": "做完了.", "delay_ms": 300}
]
```

每次模型调用消费一条, 按顺序往下走. **文件每次调用都重读**, 所以可以一边跑一边改后面
几条 —— 改完存盘, 下一次调用就按新的来.

脚本用完之后一律回一条纯文本, 于是那一轮以 FINAL_ANSWER 收场. 不循环, 也不重复最后一
条: 那两种做法都会让一个写错的脚本变成一个转不完的循环, 而它看起来像是模型在打转.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.domain.model.request import ModelRequest, StructuredModelRequest
from forgecli.domain.model.response import (
    FinishReason,
    ModelResponse,
    ModelUsage,
    StructuredModelResponse,
)
from forgecli.domain.model.streaming import ModelStreamChunk, ToolCallDelta
from forgecli.domain.tool.tool_call import ToolCall

__all__ = ["MOCK_MODEL", "MOCK_PROVIDER", "MockLlmGateway", "write_example_script"]

MOCK_PROVIDER = "mock"
MOCK_MODEL = "mock"

# 流式一次吐多少个字符. 取小值是为了让终端上那段"逐字出现"真的看得见 —— 这个网关存在的
# 意义之一就是肉眼验收流式渲染.
_STREAM_CHUNK_CHARS = 8

# 粗估 token: 4 个字符算一个. 只为了让 usage 链路上有非零数字可看, 不追求准确 ——
# 所有产出都标 estimated=True.
_CHARS_PER_TOKEN = 4

_EXHAUSTED = "[mock] 脚本已经用完了. 往脚本文件里再加几条就能接着演."

_PARSE_FAILED = "[mock] 脚本读不了: {reason}. 改好文件再说一句话就会重读."


@dataclass(frozen=True)
class _Scripted:
    """脚本里的一条. 空的 text 加空的 tool_calls 也合法 —— 那是一次空回复."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    # 回话前先等这么久. 用来把 ask_user 的卡片留在屏幕上看清楚, 或者看流式效果.
    delay_ms: int = 0
    # 只给 complete_structured 用.
    data: object = field(default=None)

    @property
    def finish_reason(self) -> FinishReason:
        return FinishReason.TOOL_CALLS if self.tool_calls else FinishReason.STOP


class MockLlmGateway(LlmGateway):
    """按脚本逐条回话.

    线程安全性与真网关一致: 每个 ProjectRuntime 一个实例, 而同一时间只跑一轮.
    """

    def __init__(
        self,
        script: Path,
        *,
        sleep: object = time.sleep,
        clock: object = time.monotonic,
    ) -> None:
        self._script = script
        self._sleep = sleep
        self._clock = clock
        self._index = 0

    # ---- LlmGateway ----

    def complete(self, request: ModelRequest) -> ModelResponse:
        started = self._now()
        scripted = self._next()
        self._wait(scripted)
        return self._response(request, scripted, started)

    def stream(self, request: ModelRequest) -> Iterator[ModelStreamChunk]:
        scripted = self._next()
        self._wait(scripted)
        sequence = 0
        for piece in _slices(scripted.text, _STREAM_CHUNK_CHARS):
            if _cancelled(request):
                yield self._chunk(sequence, interrupted=True, usage=_usage(request, ""))
                return
            yield self._chunk(sequence, delta_text=piece)
            sequence += 1
        if scripted.tool_calls:
            # 一次调用一条 delta, 参数整段给: 累积器按 index 聚合, 拆不拆开对它没区别,
            # 而拆开只会让这份脚本更难读.
            yield self._chunk(
                sequence,
                tool_call_deltas=tuple(
                    ToolCallDelta(
                        index=index,
                        tool_call_id=call.tool_call_id,
                        name=call.name,
                        arguments_delta=json.dumps(
                            dict(call.arguments), ensure_ascii=False
                        ),
                    )
                    for index, call in enumerate(scripted.tool_calls)
                ),
            )
            sequence += 1
        # 收尾块自带 usage 与 finish_reason (ADR-0011 §9): 少了它们, 上游拿不到用量,
        # 也判不出这一轮该继续还是该收.
        yield self._chunk(
            sequence,
            usage=_usage(request, scripted.text),
            finish_reason=scripted.finish_reason,
        )

    def complete_structured(
        self, request: StructuredModelRequest
    ) -> StructuredModelResponse:
        """结构化输出. 目前**全库没有调用方**, 所以这里只做到"不骗人"的程度:

        回脚本里的 `data`, 没写就回一个空对象, 并且不按 schema 校验 —— 校验是真网关的
        职责, 在这里重写一遍等于多一份会与它漂移的实现.
        """
        started = self._now()
        scripted = self._next()
        self._wait(scripted)
        inner = request.model_request
        return StructuredModelResponse(
            request_id=inner.request_id,
            provider=MOCK_PROVIDER,
            model=MOCK_MODEL,
            data=scripted.data if scripted.data is not None else {},
            usage=_usage(inner, ""),
            latency_ms=self._elapsed_ms(started),
        )

    # ---- 脚本 ----

    def _next(self) -> _Scripted:
        """取下一条. 每次都重读文件, 于是可以一边跑一边改后面几条.

        读不了或格式不对时回一条说明文字, **不抛异常**: 编辑到一半的 JSON 很常见, 而为
        它炸掉整个会话, 用户就得重开一次并把上下文丢掉.
        """
        index = self._index
        self._index += 1
        try:
            entries = _load(self._script)
        except (OSError, ValueError) as exc:
            return _Scripted(text=_PARSE_FAILED.format(reason=exc))
        if index >= len(entries):
            return _Scripted(text=_EXHAUSTED)
        return _entry(entries[index], index)

    def _wait(self, scripted: _Scripted) -> None:
        if scripted.delay_ms > 0:
            self._sleep(scripted.delay_ms / 1000.0)  # type: ignore[operator]

    # ---- 组装 ----

    def _response(
        self, request: ModelRequest, scripted: _Scripted, started: float
    ) -> ModelResponse:
        return ModelResponse(
            request_id=request.request_id,
            provider=MOCK_PROVIDER,
            model=MOCK_MODEL,
            content=scripted.text,
            finish_reason=scripted.finish_reason,
            usage=_usage(request, scripted.text),
            latency_ms=self._elapsed_ms(started),
            tool_calls=scripted.tool_calls,
        )

    def _chunk(
        self,
        sequence: int,
        *,
        delta_text: str | None = None,
        tool_call_deltas: tuple[ToolCallDelta, ...] = (),
        usage: ModelUsage | None = None,
        finish_reason: FinishReason | None = None,
        interrupted: bool = False,
    ) -> ModelStreamChunk:
        return ModelStreamChunk(
            request_id=self._request_id,
            sequence=sequence,
            provider=MOCK_PROVIDER,
            model=MOCK_MODEL,
            delta_text=delta_text,
            tool_call_deltas=tool_call_deltas,
            usage_delta=usage,
            finish_reason=FinishReason.USER_CANCELLED if interrupted else finish_reason,
            interrupted=interrupted,
        )

    _request_id = "mock"

    def _now(self) -> float:
        return float(self._clock())  # type: ignore[operator]

    def _elapsed_ms(self, started: float) -> float:
        return max(0.0, (self._now() - started) * 1000.0)


def _cancelled(request: ModelRequest) -> bool:
    token = request.cancel_token
    return token is not None and token.cancelled


def _usage(request: ModelRequest, text: str) -> ModelUsage:
    incoming = sum(len(_text_of(message)) for message in request.messages)
    input_tokens = incoming // _CHARS_PER_TOKEN
    output_tokens = len(text) // _CHARS_PER_TOKEN
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        estimated=True,
    )


def _text_of(message: object) -> str:
    """粗略量一条消息有多长. 只用于估 token, 取不到就算 0."""
    content = getattr(message, "content", ())
    return "".join(str(getattr(block, "text", "")) for block in content)


def _slices(text: str, size: int) -> list[str]:
    return [text[start : start + size] for start in range(0, len(text), size)]


def _load(script: Path) -> Sequence[object]:
    raw = json.loads(script.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    if isinstance(raw, Mapping):
        responses = raw.get("responses")
        if isinstance(responses, list):
            return responses
    raise ValueError("脚本顶层要么是数组, 要么是带 responses 数组的对象")


def _entry(raw: object, index: int) -> _Scripted:
    if not isinstance(raw, Mapping):
        return _Scripted(text=str(raw))
    delay = raw.get("delay_ms", 0)
    return _Scripted(
        text=str(raw.get("text", "")),
        tool_calls=_tool_calls(raw.get("tool_calls"), index),
        delay_ms=delay if isinstance(delay, int) and delay > 0 else 0,
        data=raw.get("data"),
    )


def _tool_calls(raw: object, index: int) -> tuple[ToolCall, ...]:
    items: Sequence[object] = raw if isinstance(raw, list) else ()
    calls: list[ToolCall] = []
    for position, item in enumerate(items):
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        arguments = item.get("arguments")
        calls.append(
            ToolCall(
                # id 由位置派生而不是随机: 同一条脚本重跑两次, 日志里的 id 也一样,
                # 于是两次运行可以逐行对比.
                tool_call_id=str(item.get("id", "") or f"mock_{index}_{position}"),
                name=name,
                arguments=dict(arguments) if isinstance(arguments, Mapping) else {},
            )
        )
    return tuple(calls)


_EXAMPLE: list[object] = [
    {
        "_readme": [
            "每次模型调用消费一条, 按顺序往下走. 文件每次调用都重读, 可以边跑边改.",
            "一条可以同时有 text 与 tool_calls; 只有 tool_calls 就是纯工具调用那一步.",
            "delay_ms 让这一条先等一会再回, 用来看清流式或卡片.",
            "脚本用完之后一律回一句提示并结束那一轮, 不循环.",
        ]
    },
    {"text": "我先看看这个仓库里有什么.", "delay_ms": 200},
    {"tool_calls": [{"name": "tree_view", "arguments": {"path": "."}}]},
    {
        "text": "结构看完了, 有件事要先问你.",
        "tool_calls": [
            {
                "name": "ask_user",
                "arguments": {
                    "question": "接下来先补测试还是先改实现?",
                    "options": [
                        {"value": "tests", "label": "先补测试"},
                        {"value": "impl", "label": "先改实现"},
                    ],
                },
            }
        ],
    },
    {"text": "知道了, 就按你说的来. 这次演示到此为止."},
]


def write_example_script(script: Path) -> None:
    """写一份可以直接跑的示例脚本.

    指定了 `--mock-llm` 却没有那个文件时调用它 —— 比报一句"文件不存在"有用得多: 这个
    功能的第一次使用几乎总是"我还不知道该写什么".
    """
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        json.dumps(_EXAMPLE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
