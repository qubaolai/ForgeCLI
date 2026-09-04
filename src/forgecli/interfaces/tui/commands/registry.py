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
    provider,
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


# 请求这条命令自己的说明. 与大多数命令行工具一致, 两种写法都收.
HELP_FLAGS = frozenset({"-h", "--help"})


@dataclass(frozen=True)
class Command:
    """一条命令.

    ``detail`` 是 `/<命令> -h` 才展开的那几句. 它不进 `/help` 的清单 —— 二十条命令
    每条三行, 那张表就没法扫了; 而进菜单时重复一遍命令是干什么的更糟, 用户刚敲完它.
    """

    name: str
    summary: str
    handler: Handler
    usage: str = ""
    detail: str = ""


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
            "直接输入文字就是和模型说话. 输入 / 弹出命令菜单, ↑↓ 选, 回车确认; "
            "菜单没开时 Tab / Shift-Tab 切模式. Ctrl+J 换行, Ctrl-C 停止这一轮, "
            "空行连按两次 Ctrl-C 退出.\n"
            "想知道某条命令具体怎么用: /<命令> -h",
            style=STYLE_DIM,
        )
    )


def show_command_help(context: CommandContext, command: Command) -> None:
    """`/<命令> -h`: 只讲这一条."""
    context.console.print(Text(command.usage or command.name, style=STYLE_ACCENT))
    context.console.print(Text(f"  {command.summary}", style=STYLE_DIM))
    for line in command.detail.splitlines():
        context.console.print(Text(f"  {line}", style=STYLE_DIM))


COMMANDS: tuple[Command, ...] = (
    Command("/help", "列出全部命令", cmd_help),
    Command("/status", "会话, 模式, 模型与目录的当前状态", session.cmd_status),
    Command("/new", "开一个新会话", session.cmd_new),
    Command("/sessions", "列出并恢复历史会话", session.cmd_sessions),
    Command("/resume", "恢复指定会话", session.cmd_resume, usage="/resume <会话 id>"),
    Command(
        "/mode",
        "隔离档与审批档",
        mode.cmd_mode,
        usage="/mode [预设名]",
        detail=(
            "两个轴独立: 隔离档管围栏允许什么, 审批档管什么时候要你点头.\n"
            "预设名 plan / accept_edits / auto / full_access 是常用组合的快捷方式.\n"
            "输入框里 Tab / Shift-Tab 也能沿这条梯度切档."
        ),
    ),
    Command(
        "/model",
        "当前模型, 按用途覆盖, 模型参数",
        model.cmd_model,
        usage="/model [provider:model]",
        detail=(
            "带参数直接切当前模型; 不带参数进菜单.\n"
            "供应商 (地址, 协议, 密钥) 不在这里, 归 /provider."
        ),
    ),
    Command(
        "/provider",
        "供应商: 添加与编辑接入点",
        provider.cmd_provider,
        usage="/provider [add]",
        detail=(
            "供应商是接入点: 地址, 协议, 密钥取哪个环境变量.\n"
            "内置几家不用先添加, 直接在 /model 里给它加模型即可."
        ),
    ),
    Command(
        "/thinking",
        "当前模型的思考开关与强度",
        model.cmd_thinking,
        usage="/thinking [on|off|强度]",
        detail=(
            "只改本进程, 不落盘 —— 与模型配置里的持久 thinking 是两件事.\n"
            "可选强度由当前模型自己声明; 模型没声明就只有开关."
        ),
    ),
    Command("/gateway", "网关的缓存, 熔断与重试", model.cmd_gateway),
    Command(
        "/config",
        "配置面",
        config.cmd_config,
        usage="/config [键] [值]",
        detail=(
            "按前缀分类, ←/→ 换分类.\n"
            "带键名直接改一项, 例如 /config logging.level debug.\n"
            '模型, 供应商与网关在"模型"这一类里有入口.'
        ),
    ),
    Command("/tools", "本档模式下模型看得见哪些工具", security.cmd_tools),
    Command(
        "/rules",
        "学习到的放行规则",
        security.cmd_rules,
        usage="/rules [prune]",
        detail=(
            '人类明确选过"本工作区始终允许"才会有规则; 模型不能创建或扩大它们.\n'
            "prune 清掉过期或不再匹配当前执行画像的那些."
        ),
    ),
    Command(
        "/dirs",
        "工作区目录与读写授权",
        security.cmd_dirs,
        usage="/dirs [路径]",
        detail=(
            "额外目录重启后一律按只读恢复: 项目配置只存路径, 不存写授权.\n"
            "要写就在这里重新升一次 —— 安全上宁可多问一次."
        ),
    ),
    Command("/projects", "项目中心", project.cmd_projects, usage="/projects [路径]"),
    Command(
        "/plan", "计划目录与活动计划", planning.cmd_plan, usage="/plan [review|计划 id]"
    ),
    Command("/todo", "当前待办清单", planning.cmd_todo),
    Command(
        "/checkpoints",
        "恢复点: 预览与还原",
        recovery.cmd_checkpoints,
        detail="冲突项默认跳过, 不静默覆盖: 恢复点之后的改动不在任何恢复点里.",
    ),
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
    if command is not None and argument.strip() in HELP_FLAGS:
        # -h 在命令处理函数之前拦: 让每条命令自己认这个参数, 就会有一半忘记认,
        # 而忘记的那些会把 "-h" 当成一个真的参数值去用.
        show_command_help(context, command)
        return
    if command is None:
        error(context.console, f"没有这条命令: {name}")
        close = difflib.get_close_matches(name, _BY_NAME, n=3)
        hint = " ".join(close) if close else "/help"
        context.console.print(Text(f"  试试 {hint}", style=STYLE_ACCENT))
        return
    command.handler(context, argument)
