"""人工 Shell 的信任边界 (ADR-0017 §2, §3, §15.1).

这里锁的是整份 ADR 里最要紧的一条: **`#` 的特权来自输入来源, 不是字符本身**.
只要这条守住, "让模型说一句 # 就拿到不受裁决的 Shell"就不成立.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from forgecli.application.intent_router import IntentRouter
from forgecli.application.slash_commands.registry import CommandRegistry, CommandSpec
from forgecli.domain.intents import (
    InputOrigin,
    ManualShellIntent,
    SlashCommand,
    UserMessage,
)
from forgecli.interfaces.cli.commands.help_command import HelpCommand
from forgecli.interfaces.cli.output import RichOutput


def _router() -> IntentRouter:
    registry = CommandRegistry()
    registry.register(
        CommandSpec("help", "帮助", handler=HelpCommand(registry, RichOutput(None)))  # type: ignore[arg-type]
    )
    return IntentRouter(registry=registry)


# ---- 来源决定特权 ----


def test_a_bare_hash_from_the_tty_enters_shell_mode() -> None:
    intent = _router().route("#", origin=InputOrigin.TTY_USER)
    assert isinstance(intent, ManualShellIntent)


@pytest.mark.parametrize("text", ["#", " # ", "#\n"])
def test_surrounding_whitespace_is_trimmed(text: str) -> None:
    assert isinstance(
        _router().route(text, origin=InputOrigin.TTY_USER), ManualShellIntent
    )


def test_the_same_hash_from_a_program_is_just_text() -> None:
    """模型文本, 工具输出, 事件重放都走这条路. 一个 `#` 不该因为长得像就拿到特权."""
    assert isinstance(_router().route("#", origin=InputOrigin.PROGRAM), UserMessage)


def test_a_hash_from_the_web_is_just_text() -> None:
    """Web 用户经 Agent 安全工具链工作，不继承终端人工 Shell 的旁路特权。"""
    assert isinstance(_router().route("#", origin=InputOrigin.WEB_USER), UserMessage)


def test_the_default_origin_is_not_privileged() -> None:
    """忘记传 origin 的后果必须是**少**一项特权, 不是多一项."""
    assert isinstance(_router().route("#"), UserMessage)


@pytest.mark.parametrize("text", ["#!/bin/sh", "## 二级标题", "#comment", "a #"])
def test_text_without_a_space_after_the_hash_is_not_shell(text: str) -> None:
    """裸 `#` 开会话, `# 命令` 跑一条, 其余照常发给模型.

    `# 命令` 这条路由的测试在 test_one_shot.py; 这里只钉"哪些 `#` 开头的东西不是 shell".
    """
    intent = _router().route(text, origin=InputOrigin.TTY_USER)
    assert isinstance(intent, UserMessage)


def test_slash_commands_are_unaffected() -> None:
    intent = _router().route("/help", origin=InputOrigin.TTY_USER)
    assert isinstance(intent, SlashCommand)


# ---- 值对象自己就是凭据 ----


def test_the_intent_cannot_be_built_with_a_program_origin() -> None:
    """校验放在构造函数而不是路由里: 路由能绕过, 构造函数不行."""
    with pytest.raises(ValueError, match="TTY"):
        ManualShellIntent(raw_text="#", origin=InputOrigin.PROGRAM)


# ---- 静态边界: 人工 Shell 碰不到 Agent 侧 ----

_FORBIDDEN = (
    "forgecli.application.tool_request",
    "forgecli.application.tools",
    "forgecli.application.security",
    "forgecli.application.agent_loop",
    "forgecli.application.recovery",
    "forgecli.application.llm",
)


def _imports_of(package: Path) -> set[str]:
    found: set[str] = set()
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
    return found


def test_the_manual_shell_package_never_reaches_the_agent_side() -> None:
    """§2: 两条路径不得互相调用或降级.

    只要这里能拿到 ToolRequestCoordinator, "人工 Shell 顺便记一条 learned allow rule"
    这种看起来很贴心的改动就会有人做 —— 而它等于让用户手敲的命令替 Agent 拿到授权.

    scripts/check_arch.py 里也有同样的规则; 这条测试是给"忘了跑 make arch"的情况兜底.
    """
    root = Path(__file__).resolve().parents[2]
    imports = _imports_of(root / "src" / "forgecli" / "application" / "manual_shell")
    leaked = {
        name
        for name in imports
        for banned in _FORBIDDEN
        if name == banned or name.startswith(f"{banned}.")
    }
    assert leaked == set(), f"人工 Shell 不该认识这些模块: {sorted(leaked)}"


def test_the_manual_shell_domain_stays_platform_free() -> None:
    """§13: domain/manual_shell 不依赖 subprocess 或平台模块."""
    root = Path(__file__).resolve().parents[2]
    imports = _imports_of(root / "src" / "forgecli" / "domain" / "manual_shell")
    for banned in ("subprocess", "os", "pty", "termios", "signal", "rich"):
        assert banned not in imports
