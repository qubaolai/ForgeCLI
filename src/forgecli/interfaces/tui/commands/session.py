"""会话相关的斜杠命令: 状态, 新开, 列出与恢复."""

from __future__ import annotations

from rich.markdown import Markdown
from rich.text import Text

from forgecli.domain.session.events import EventType
from forgecli.interfaces.tui.chooser import Option, choose
from forgecli.interfaces.tui.commands.context import CommandContext
from forgecli.interfaces.tui.console import (
    STYLE_ACCENT,
    STYLE_DIM,
    error,
    kv_table,
    listing,
    ok,
    rule,
    truncate,
)
from forgecli.shared.errors import SessionStateError

# 会话列表里标题截多长. 标题是从第一条用户消息切出来的, 可能是一整段话.
_TITLE_WIDTH = 56


def cmd_exit(context: CommandContext, _argument: str) -> None:
    context.exit_requested = True


def cmd_clear(context: CommandContext, _argument: str) -> None:
    """只清屏, 不动会话. 想要一段干净的上下文用 /new."""
    context.console.clear()


def cmd_status(context: CommandContext, _argument: str) -> None:
    runtime = context.runtime
    snapshot = runtime.session.current()
    model = runtime.current_model()
    thinking = runtime.thinking_view()
    planning = runtime.tools.planning.load()
    rows = [
        ("项目", runtime.project.project_id),
        ("主工作区", runtime.project.primary_workspace_root),
        ("会话", snapshot.session_id),
        ("模式", snapshot.mode.value),
        ("模型", str(model) if model is not None else "未设置"),
    ]
    if thinking.get("configured"):
        effort = str(thinking.get("effort", "") or "-")
        rows.append(("thinking", f"{thinking.get('mode', '')} · {effort}"))
    extra = runtime.project.workspace_roots[1:]
    if extra:
        rows.append(("额外目录", "\n".join(extra)))
    if planning.plan is not None:
        rows.append(("计划", f"{planning.plan.title} · {planning.plan.status.value}"))
    if planning.todo is not None:
        done = sum(1 for item in planning.todo.items if item.status.value == "done")
        rows.append(("待办", f"{done}/{len(planning.todo.items)}"))
    rows.append(("状态", "正在跑一轮" if runtime.busy else "空闲"))
    rule(context.console, "当前状态")
    context.console.print(kv_table(rows))


def cmd_new(context: CommandContext, _argument: str) -> None:
    if not context.require_idle():
        return
    snapshot = context.runtime.new_session()
    ok(context.console, f"新会话 {snapshot.session_id}")


def cmd_sessions(context: CommandContext, argument: str) -> None:
    """列出历史会话; 带参数或选中一条就直接恢复它."""
    runtime = context.runtime
    if argument.strip():
        _resume(context, argument.strip())
        return
    sessions = runtime.list_sessions()
    if not sessions:
        context.console.print(Text("还没有历史会话", style=STYLE_DIM))
        return
    current = runtime.session.current().session_id
    rule(context.console, "历史会话")
    context.console.print(
        listing(
            ("会话", "更新于", "标题"),
            (
                (
                    item.session_id + (" ●" if item.session_id == current else ""),
                    item.updated_at,
                    truncate(item.title or "(无标题)", _TITLE_WIDTH),
                )
                for item in sessions
            ),
        )
    )
    picked = choose(
        context.console,
        "恢复哪一个",
        [
            Option(item.session_id, item.session_id, truncate(item.title, 40))
            for item in sessions
        ],
        current=current,
    )
    if picked is not None:
        _resume(context, picked.key)


def cmd_resume(context: CommandContext, argument: str) -> None:
    session_id = argument.strip()
    if not session_id:
        cmd_sessions(context, "")
        return
    _resume(context, session_id)


def _resume(context: CommandContext, session_id: str) -> None:
    if not context.require_idle():
        return
    runtime = context.runtime
    try:
        snapshot = runtime.resume(session_id)
    except SessionStateError as exc:
        error(context.console, exc.message)
        return
    except RuntimeError as exc:
        error(context.console, str(exc))
        return
    ok(context.console, f"已恢复会话 {snapshot.session_id}")
    _replay(context, session_id)


def _replay(context: CommandContext, session_id: str) -> None:
    """把这条会话的对话正文重放一遍.

    只重放用户与助手的消息, 不重放处理过程: 过程事件活在进程内 (ADR-0016 §9), 换一个
    进程再打开这条会话时它们本来就不存在 —— 拿 transcript 拼一份出来只会是假的.
    """
    runtime = context.runtime
    events = runtime.transcript(session_id)
    if not events:
        return
    rule(context.console, "会话回放")
    for event in events:
        text = str(event.payload.get("text", ""))
        if not text:
            continue
        if event.type is EventType.USER_MESSAGE:
            context.console.print(Text(f"› {text}", style=STYLE_ACCENT))
        else:
            context.console.print(Markdown(text))
        context.console.print()
