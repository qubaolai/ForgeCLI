"""会话内输入解析：把一行原始输入路由为某个 UserIntent。

IntentRouter 是纯解析器：只依赖领域值对象与 CommandRegistry，不碰 Rich/Typer/LLM/
文件系统；无副作用、不执行命令；斜杠命令绝不交给模型自由解释(docs/05 §6)。

优先级：
    1. 空白            -> 拒绝(REPL 已过滤，兜底)。
    2. 不以 "/" 开头    -> UserMessage(自然语言)。
    3. 裸 "/"          -> 等价 /help，强制展示可用命令。
    4. 合法斜杠语法     -> 查 registry：MODE_CHANGE/CONTROL/SLASH_COMMAND/未注册。
    5. "/" 开头但语法非法(/help是做什么用的、/usr/bin/env) -> UserMessage。

判别核心：命令名后必须紧跟空白或行尾——空格是"这是命令调用"的唯一信号。
"""

from __future__ import annotations

import re

from forgecli.application.slash_commands.registry import CommandRegistry
from forgecli.domain.intents import (
    ControlSignal,
    IntentKind,
    ModeChange,
    SlashCommand,
    UnknownCommand,
    UserIntent,
    UserMessage,
)

__all__ = ["IntentRouter"]

_SLASH_RE = re.compile(
    r"^/(?P<name>[A-Za-z][A-Za-z0-9_-]*)(?:\s+(?P<rest>.+))?$",
    re.DOTALL,
)


class IntentRouter:
    def __init__(self, registry: CommandRegistry) -> None:
        self._registry = registry

    def route(self, raw_text: str) -> UserIntent:
        if not raw_text or not raw_text.strip():
            raise ValueError("raw_text 不能为空")
        text = raw_text.strip()

        if not text.startswith("/"):
            return UserMessage(raw_text=raw_text)
        if text.strip("/") == "":  # 裸斜杠
            return SlashCommand(raw_text=raw_text, command="help")

        match = _SLASH_RE.match(text)
        if match is None:  # "/" 开头但不是合法命令语法 -> 自然语言
            return UserMessage(raw_text=raw_text)

        name = match.group("name").lower()
        rest = match.group("rest")
        args = tuple(rest.split()) if rest else ()
        return self._classify(raw_text, name, args)

    def _classify(self, raw_text: str, name: str, args: tuple[str, ...]) -> UserIntent:
        spec = self._registry.get(name)
        if spec is None:
            return UnknownCommand(
                raw_text=raw_text,
                command=name,
                error_message=f"未知命令 /{name}。输入 /help 查看可用命令。",
            )
        if spec.kind is IntentKind.MODE_CHANGE:
            assert spec.mode is not None  # registry 不变量已保证
            return ModeChange(raw_text=raw_text, target_mode=spec.mode)
        if spec.kind is IntentKind.CONTROL:
            assert spec.action is not None
            return ControlSignal(raw_text=raw_text, action=spec.action)
        return SlashCommand(raw_text=raw_text, command=name, args=args)
