"""斜杠命令表.

一条命令 = 表里一行. 名字, 一句说明和处理函数住在一起 —— 分成"名字表"与"处理函数表"
两份的话, 加一条命令时漏改哪一份都不会报错, 只会让它补全得出来但敲不动, 或者反过来.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable
from dataclasses import dataclass

from rich.text import Text

from forgecli.interfaces.tui.commands import (
    config,
    mode,
    model,
    planning,
    project,
    recovery,
    security,
    session,
)
from forgecli.interfaces.tui.commands.context import CommandContext
from forgecli.interfaces.tui.console import (
    STYLE_ACCENT,
    STYLE_DIM,
    error,
    listing,
    rule,
)

Handler = Callable[[CommandContext, str], None]


@dataclass(frozen=True)
class Command:
    name: str
    summary: str
    handler: Handler
    usage: str = ""


def cmd_help(context: CommandContext, _argument: str) -> None:
    rule(context.console, "斜杠命令")
    context.console.print(
        listing(
            ("命令", "说明"),
            ((item.usage or item.name, item.summary) for item in COMMANDS),
        )
    )
    context.console.print()
    context.console.print(
        Text(
            "直接输入文字就是和模型说话; 行尾加 \\ 可以继续下一行. "
            "Tab 补全斜杠命令, Ctrl-C 停止当前一轮, Ctrl-D 退出.",
            style=STYLE_DIM,
        )
    )


COMMANDS: tuple[Command, ...] = (
    Command("/help", "列出全部命令", cmd_help),
    Command("/status", "会话, 模式, 模型与目录的当前状态", session.cmd_status),
    Command("/new", "开一个新会话", session.cmd_new),
    Command("/sessions", "列出并恢复历史会话", session.cmd_sessions),
    Command("/resume", "恢复指定会话", session.cmd_resume, usage="/resume <会话 id>"),
    Command("/mode", "隔离档与审批档", mode.cmd_mode, usage="/mode [预设名]"),
    Command(
        "/model",
        "当前模型, 用途覆盖, 模型与供应商配置",
        model.cmd_model,
        usage="/model [provider:model]",
    ),
    Command("/thinking", "当前模型的思考开关与强度", model.cmd_thinking),
    Command("/gateway", "网关的缓存, 熔断与重试", model.cmd_gateway),
    Command("/config", "常规配置", config.cmd_config, usage="/config [键] [值]"),
    Command("/tools", "本档模式下模型看得见哪些工具", security.cmd_tools),
    Command("/rules", "学习到的放行规则", security.cmd_rules, usage="/rules [prune]"),
    Command("/dirs", "工作区目录与读写授权", security.cmd_dirs, usage="/dirs [路径]"),
    Command("/projects", "项目中心", project.cmd_projects, usage="/projects [路径]"),
    Command(
        "/plan", "计划目录与活动计划", planning.cmd_plan, usage="/plan [review|计划 id]"
    ),
    Command("/todo", "当前待办清单", planning.cmd_todo),
    Command("/checkpoints", "恢复点: 预览与还原", recovery.cmd_checkpoints),
    Command("/undo", "还原最近一次改动", recovery.cmd_undo),
    Command("/recovery", "恢复层状态与未收尾事务", recovery.cmd_recovery),
    Command("/clear", "清屏 (不动会话)", session.cmd_clear),
    Command("/exit", "退出", session.cmd_exit),
)

_BY_NAME = {item.name: item for item in COMMANDS}


def command_names() -> tuple[str, ...]:
    return tuple(_BY_NAME)


def dispatch(context: CommandContext, line: str) -> None:
    """执行一条斜杠命令. 认不出就给最接近的几个, 不猜着执行一条."""
    name, _, argument = line.strip().partition(" ")
    command = _BY_NAME.get(name)
    if command is None:
        error(context.console, f"没有这条命令: {name}")
        close = difflib.get_close_matches(name, _BY_NAME, n=3)
        hint = " ".join(close) if close else "/help"
        context.console.print(Text(f"  试试 {hint}", style=STYLE_ACCENT))
        return
    command.handler(context, argument)
