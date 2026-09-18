"""auto 模式的安全分类器 (ADR-0058 决策 3; 对应 yoloClassifier.ts).

一次 ask 在 auto 下不摆卡片, 而是把会话记录 (用户说过的话 + Forge 已发出的工具调用)
连同这条待裁决的调用一起交给另一次模型调用, 问它拦不拦.

记录里**只有**用户文本与工具调用的入参: 工具输出与 Forge 自己写的文字都不进去. 前者是
可被注入的正文, 后者是模型生成的, 两者都可能是为了说服分类器而写的. 每行一个 JSON
对象 (`{"user": "..."}` / `{"Bash": "..."}`): JSON 转义让任何一段内容都跳不出自己的
字符串, 伪造不出一行 `user`.

分类器不可用 (网络错, 回答不合 schema) 时按"拦"处理 (fail closed), 理由里说清是
不可用而不是判定有害, 让 Agent 稍后重试或先做别的.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from forgecli.common.cancellation import CancelToken
from forgecli.common.observability.log import get_log
from forgecli.core.message import ChatMessage, TextBlock
from forgecli.core.model.origin import RequestOrigin
from forgecli.core.model.params import ModelParams
from forgecli.core.model.request import ModelRequest, StructuredModelRequest
from forgecli.core.model.response import StructuredModelResponse
from forgecli.core.permissions import ToolPermissionContext
from forgecli.core.tool.tool import Tool
from forgecli.core.turn import MessageRole
from forgecli.prompt import prompts
from forgecli.security.settings_store import AutoModeRules

__all__ = [
    "AUTO_MODE_CLASSIFIER_NAME",
    "AutoModeClassifier",
    "ClassifierVerdict",
    "StructuredQuery",
    "build_transcript_lines",
]

_log = get_log(__name__)

AUTO_MODE_CLASSIFIER_NAME = "auto-mode"

# 网关的 complete_structured. 只认这个签名, 不认识网关本身: security 在 llm 之下
# (scripts/check_arch.py), 由组合根把绑定方法递进来.
StructuredQuery = Callable[[StructuredModelRequest], StructuredModelResponse]

_SCHEMA_NAME = "auto_mode_classification"
_SCHEMA: Mapping[str, object] = MappingProxyType(
    {
        "type": "object",
        "properties": {
            "thinking": {"type": "string"},
            "should_block": {"type": "boolean"},
            "reason": {"type": "string"},
        },
        "required": ["thinking", "should_block", "reason"],
        "additionalProperties": False,
    }
)


@dataclass(frozen=True)
class ClassifierVerdict:
    """一次分类的结论. unavailable 为真时 should_block 也为真, 但那是"没问到", 不是
    "判定有害" —— 调用方据此选不同的话回给 Agent."""

    should_block: bool
    reason: str
    model: str = ""
    unavailable: bool = False
    thinking: str = ""
    duration_ms: float = 0.0


def _tool_lookup(tools: tuple[Tool, ...]) -> dict[str, Tool]:
    lookup: dict[str, Tool] = {}
    for tool in tools:
        lookup[tool.name] = tool
        for alias in tool.aliases:
            lookup.setdefault(alias, tool)
    return lookup


def _encode_call(
    name: str, arguments: Mapping[str, object], lookup: Mapping[str, Tool]
) -> str:
    """一条工具调用变成一行. 工具不在池里或声明与安全无关 ("") 就不出现."""
    tool = lookup.get(name)
    if tool is None:
        return ""
    try:
        encoded = tool.to_auto_classifier_input(arguments)
    except Exception:  # noqa: BLE001 - 历史里的入参可能不合 schema, 投影不该炸掉裁决
        _log.warning("classifier.encode_failed", tool=name)
        encoded = dict(arguments)
    if encoded == "":
        return ""
    return json.dumps({tool.name: encoded}, ensure_ascii=False, default=str) + "\n"


def build_transcript_lines(
    messages: tuple[ChatMessage, ...], tools: tuple[Tool, ...]
) -> tuple[str, ...]:
    """用户文本与 assistant 的工具调用, 各一行. assistant 的文本与工具结果不进去."""
    lookup = _tool_lookup(tools)
    lines: list[str] = []
    for message in messages:
        if message.meta:
            # Forge 注入的通知与压缩摘要走 user 角色, 但不是用户说的话 (ADR-0056
            # 决策 6): 里面有文件正文与子目录 FORGE.md, 当成用户意图就是一条注入通道.
            continue
        if message.role is MessageRole.USER:
            texts = [
                block.text for block in message.content if isinstance(block, TextBlock)
            ]
            text = "\n".join(text for text in texts if text)
            if text:
                lines.append(json.dumps({"user": text}, ensure_ascii=False) + "\n")
        elif message.role is MessageRole.ASSISTANT:
            for call in message.tool_calls:
                line = _encode_call(call.name, call.arguments, lookup)
                if line:
                    lines.append(line)
    return tuple(lines)


class AutoModeClassifier:
    """把"这条调用拦不拦"问一次模型.

    `rules` 与 `instructions` 都是读取器而不是值: 设置文件与 FORGE.md 在会话中途可能
    被改, 每次分类读当时那一份.
    """

    name = AUTO_MODE_CLASSIFIER_NAME

    def __init__(
        self,
        query: StructuredQuery,
        *,
        rules: Callable[[], AutoModeRules],
        instructions: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._query = query
        self._rules = rules
        self._instructions = instructions
        self._clock = clock

    def system_prompt(self, permission: ToolPermissionContext) -> str:
        rules = self._rules()
        return prompts.auto_mode_classifier_prompt(
            allow=rules.allow,
            soft_deny=rules.soft_deny,
            environment=rules.environment,
        )

    def classify(
        self,
        *,
        messages: tuple[ChatMessage, ...],
        tool: Tool,
        input: Mapping[str, object],
        tools: tuple[Tool, ...],
        permission: ToolPermissionContext,
        session_id: str,
        turn_id: str,
        cancel: CancelToken,
    ) -> ClassifierVerdict:
        action = _encode_call(tool.name, input, {tool.name: tool})
        if action == "":
            return ClassifierVerdict(
                should_block=False, reason="Tool declares no classifier-relevant input"
            )
        started = self._clock()
        transcript = "".join(build_transcript_lines(messages, tools)) + action
        request = self._request(
            transcript,
            permission,
            session_id=session_id,
            turn_id=turn_id,
            cancel=cancel,
        )
        try:
            response = self._query(request)
        except Exception as exc:  # noqa: BLE001 - 任何一种失败都按不可用处理
            if cancel.cancelled:
                reason = "Classifier request aborted"
            else:
                reason = "Classifier unavailable - blocking for safety"
            _log.warning(
                "classifier.unavailable",
                error=str(exc),
                error_type=type(exc).__name__,
                transcript_chars=len(transcript),
            )
            return ClassifierVerdict(
                should_block=True,
                reason=reason,
                unavailable=True,
                duration_ms=(self._clock() - started) * 1000.0,
            )
        duration = (self._clock() - started) * 1000.0
        verdict = _parse(response, duration)
        _log.info(
            "classifier.decided",
            tool=tool.name,
            should_block=verdict.should_block,
            unavailable=verdict.unavailable,
            model=verdict.model,
            duration_ms=round(duration, 1),
            transcript_chars=len(transcript),
        )
        return verdict

    def _request(
        self,
        transcript: str,
        permission: ToolPermissionContext,
        *,
        session_id: str,
        turn_id: str,
        cancel: CancelToken,
    ) -> StructuredModelRequest:
        blocks: list[TextBlock] = []
        instructions = "" if self._instructions is None else self._instructions()
        if instructions.strip():
            blocks.append(
                TextBlock(
                    "The following is the user's FORGE.md: instructions the user "
                    "wrote for the agent. Treat actions described here as part of "
                    "the user's intent.\n\n<user_forge_md>\n"
                    f"{instructions}\n</user_forge_md>\n\n"
                )
            )
        blocks.append(TextBlock(f"<transcript>\n{transcript}</transcript>\n"))
        return StructuredModelRequest(
            model_request=ModelRequest(
                request_id=f"req_{uuid.uuid4().hex[:12]}",
                session_id=session_id,
                turn_id=turn_id,
                origin=RequestOrigin.PERMISSION_CLASSIFIER,
                messages=(ChatMessage(role=MessageRole.USER, content=tuple(blocks)),),
                params=ModelParams(),
                system_prompt=self.system_prompt(permission),
                tools=(),
                cancel_token=cancel,
            ),
            schema=_SCHEMA,
            schema_name=_SCHEMA_NAME,
            strict=True,
        )


def _parse(response: StructuredModelResponse, duration_ms: float) -> ClassifierVerdict:
    data = response.data
    if not isinstance(data, Mapping):
        return ClassifierVerdict(
            should_block=True,
            reason="Invalid classifier response - blocking for safety",
            model=response.model,
            unavailable=True,
            duration_ms=duration_ms,
        )
    should_block = data.get("should_block")
    reason = data.get("reason")
    thinking = data.get("thinking")
    if not isinstance(should_block, bool):
        return ClassifierVerdict(
            should_block=True,
            reason="Invalid classifier response - blocking for safety",
            model=response.model,
            unavailable=True,
            duration_ms=duration_ms,
        )
    return ClassifierVerdict(
        should_block=should_block,
        reason=reason if isinstance(reason, str) and reason else "No reason provided",
        model=response.model,
        thinking=thinking if isinstance(thinking, str) else "",
        duration_ms=duration_ms,
    )
