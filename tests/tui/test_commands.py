"""斜杠命令表的判据.

最重要的一条是最后那个: 终端入口回来之后, "斜杠命令能改的每一项配置 Web 都要有入口"
(ADR-0025 决策 2 / 决策 33) 反过来也要成立 —— 否则又会出现"只能在某一边改"的配置,
而那正是 ADR-0025 当初要消除的状态.
"""

from __future__ import annotations

import io
import pathlib

import pytest
from rich.console import Console

from forgecli.application.project.project_service import ProjectService
from forgecli.infrastructure.project import (
    JsonProjectConfigStore,
    JsonProjectIndexStore,
)
from forgecli.interfaces.runtime.project_runtime import ProjectRuntimeRegistry
from forgecli.interfaces.tui.commands.context import CommandContext, NoActiveProject
from forgecli.interfaces.tui.commands.registry import (
    COMMANDS,
    command_names,
    dispatch,
)

# Web 上的每一块面板对应哪条命令. 这张表就是"两条入口能改的东西一样多"的判据;
# 删掉一条命令, 这里会红.
WEB_SURFACE_TO_COMMAND = {
    "模式菜单": "/mode",
    "常规配置页": "/config",
    "当前模型选择器": "/model",
    "用途模型覆盖": "/model",
    "thinking 开关与强度": "/thinking",
    "网关运行时": "/gateway",
    "安全页工具清单": "/tools",
    "学习规则与清理": "/rules",
    "工作区授权": "/dirs",
    "项目中心": "/projects",
    "计划目录": "/plan",
    "待办清单": "/todo",
    "恢复页": "/checkpoints",
    "撤销": "/undo",
    "恢复层状态": "/recovery",
    "状态页": "/status",
    "会话列表与恢复": "/sessions",
}


@pytest.fixture
def screen() -> io.StringIO:
    return io.StringIO()


@pytest.fixture
def context(screen: io.StringIO, tmp_path: pathlib.Path) -> CommandContext:
    console = Console(file=screen, width=100, no_color=True, highlight=False)
    # 真的 registry, 只是指向一个空的临时目录: 没有激活项目正是这一组要覆盖的状态.
    projects = ProjectService(
        JsonProjectIndexStore(tmp_path / "index.json"), JsonProjectConfigStore(tmp_path)
    )
    return CommandContext(console=console, registry=ProjectRuntimeRegistry(projects))


def test_command_names_are_unique_and_slash_prefixed() -> None:
    names = [item.name for item in COMMANDS]
    assert names == sorted(set(names), key=names.index)
    assert all(name.startswith("/") for name in names)
    assert set(command_names()) == set(names)


def test_help_lists_every_command(context: CommandContext, screen: io.StringIO) -> None:
    dispatch(context, "/help")
    output = screen.getvalue()
    for item in COMMANDS:
        assert item.name in output


def test_unknown_command_suggests_instead_of_guessing(
    context: CommandContext, screen: io.StringIO
) -> None:
    """猜着执行一条是最坏的处理: 用户打错的那一次会真的发生点什么."""
    dispatch(context, "/moed")
    output = screen.getvalue()
    assert "没有这条命令" in output
    assert "/mode" in output


def test_commands_needing_a_project_say_so(context: CommandContext) -> None:
    """没有激活项目时提前收场, 而不是在某个 None 上炸开."""
    with pytest.raises(NoActiveProject):
        _ = context.runtime


def test_every_web_surface_has_a_terminal_entry() -> None:
    available = set(command_names())
    missing = {
        surface: command
        for surface, command in WEB_SURFACE_TO_COMMAND.items()
        if command not in available
    }
    assert missing == {}


def test_exit_is_a_request_not_a_control_flow_jump(context: CommandContext) -> None:
    """命令只表达意愿, 由 REPL 决定怎么收场 —— 两条控制流会对"能不能读下一行"打架."""
    assert not context.exit_requested
    dispatch(context, "/exit")
    assert context.exit_requested


def test_interrupting_a_menu_does_not_kill_the_session(
    context: CommandContext, monkeypatch: pytest.MonkeyPatch, screen: io.StringIO
) -> None:
    """命令里的菜单停在 input() 上; 在那里按 Ctrl-C 是这一步不做了, 不是退出 Forge.

    不接住的话, KeyboardInterrupt 会一路冒出 REPL 并以 130 结束进程 —— 从 make 里起
    的时候那就是一行 `Error 130`, 而用户只是选错了菜单想退出来.
    """
    from rich.console import Console

    from forgecli.interfaces.exit_codes import ExitCode
    from forgecli.interfaces.tui import session_app as module

    def fake_dispatch(ctx: CommandContext, line: str) -> None:
        if line == "/exit":
            ctx.exit_requested = True
            return
        raise KeyboardInterrupt

    lines = iter(["/mode", "/plan", "/exit"])
    monkeypatch.setattr(module, "dispatch", fake_dispatch)
    monkeypatch.setattr(module.LineEditor, "read", lambda self, prompt: next(lines))
    app = module.SessionApp(
        Console(file=screen, width=100, no_color=True, highlight=False),
        context.registry,
    )
    app.context = context

    assert app.run() == ExitCode.OK
    # 两次中断各说一次"已取消", 而且第三条命令仍然读得到 —— 会话活着.
    assert screen.getvalue().count("已取消") == 2
    assert context.exit_requested
