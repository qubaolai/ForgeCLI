"""会话内输入解析：把一行原始输入路由为某个 UserIntent。

IntentRouter 是纯解析器：只依赖领域值对象与 CommandRegistry，不碰 Rich/Typer/LLM/
文件系统；无副作用、不执行命令；斜杠命令绝不交给模型自由解释(docs/05 §6)。

优先级：
    1. 空白            -> 拒绝(REPL 已过滤，兜底)。
    2. 单独 "#" 且来自前台 TTY -> ManualShellIntent(交互式会话)。
    3. 单行 "# 命令" 且来自前台 TTY -> ManualShellIntent(一次性命令)。
    4. 不以 "/" 开头    -> UserMessage(自然语言)。
    5. 裸 "/"          -> 等价 /help，强制展示可用命令。
    6. 合法斜杠语法     -> 注册了即 SlashCommand，否则 UnknownCommand。
    7. "/" 开头但语法非法(/help是做什么用的、/usr/bin/env) -> UserMessage。

判别核心：命令名后必须紧跟空白或行尾——空格是"这是命令调用"的唯一信号。

## 人工 Shell 的两个入口

    #            开一次完整交互式会话（ADR-0017 原有语义，不变）
    # clear      跑一条就回来

前缀字符只在 `ONE_SHOT_PREFIX` 一处定义，改字符改那一行即可。

## 两条约束

**来源**：`origin` 默认是 PROGRAM，只有 CLI 的前台 TTY 输入适配器会传 TTY_USER。因此
模型文本、工具输出、事件重放里出现的 `#` 一律走自然语言。

**形态**：`#` + 空格 + 单行内容。两个限制各挡一类东西：

- 要求空格挡掉 `#!/bin/sh`、`#include <stdio.h>`、`#define X`、`#123`、`## 二级标题`
  ——它们都不是 `"# "` 开头。
- 要求单行挡掉粘贴进来的 markdown 文档：`# 标题\n\n正文…` 仍是自然语言。

**这修订了 ADR-0017 决策 1**：原文写的是 `# 文本` 一律为自然语言。买下的代价是**单行**
`# 标题` 会被当成命令跑掉（多半得到一句 command not found，而 ShellModeEntry 会据此提示
"去掉开头的 # 再发一次"）。换来的是 `# clear`、`# git status` 不必为一条命令开整个会话。
"""

from __future__ import annotations

import re

from forgecli.application.slash_commands.registry import CommandRegistry
from forgecli.domain.intents import (
    InputOrigin,
    ManualShellIntent,
    SlashCommand,
    UnknownCommand,
    UserIntent,
    UserMessage,
)

__all__ = ["IntentRouter"]

ONE_SHOT_PREFIX = "# "

_SLASH_RE = re.compile(
    r"^/(?P<name>[A-Za-z][A-Za-z0-9_-]*)(?:\s+(?P<rest>.+))?$",
    re.DOTALL,
)


def _manual_shell(raw_text: str, text: str) -> ManualShellIntent | None:
    """把 `#` 与 `# 命令` 识别成人工 Shell；其余返回 None 交回正常路由。

    只在 origin 已确认为 TTY_USER 时调用。
    """
    if text == ONE_SHOT_PREFIX.strip():
        return ManualShellIntent(raw_text=raw_text)
    if not text.startswith(ONE_SHOT_PREFIX) or "\n" in text:
        return None
    command = text[2:].strip()
    # 只有 `#` 加空白：既不是命令也不像自然语言，当成开会话——比默默发给模型好。
    if not command:
        return ManualShellIntent(raw_text=raw_text)
    return ManualShellIntent(raw_text=raw_text, command=command)


class IntentRouter:
    def __init__(self, registry: CommandRegistry) -> None:
        self._registry = registry

    def route(
        self, raw_text: str, *, origin: InputOrigin = InputOrigin.PROGRAM
    ) -> UserIntent:
        if not raw_text or not raw_text.strip():
            raise ValueError("raw_text 不能为空")
        text = raw_text.strip()

        if origin is InputOrigin.TTY_USER:
            shell = _manual_shell(raw_text, text)
            if shell is not None:
                return shell
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
        # 命令是否存在是唯一判别：注册了就是 SlashCommand，具体行为交给 handler；
        # 模式切换 / 退出已不再是单独意图，无需在此分类。
        if self._registry.get(name) is None:
            return UnknownCommand(
                raw_text=raw_text,
                command=name,
                error_message=f"未知命令 /{name}。输入 /help 查看可用命令。",
            )
        return SlashCommand(raw_text=raw_text, command=name, args=args)
