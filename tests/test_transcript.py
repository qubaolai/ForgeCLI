"""transcript 渲染：用户输入与助手输出分色、可读、且不被用户输入注入样式。"""

from __future__ import annotations

import io

from rich.console import Console

from forgecli.interfaces.cli.transcript import render_assistant_turn, render_user_turn


def _console() -> tuple[Console, io.StringIO]:
    # record=True 留存样式段，便于断言颜色；同时写入 StringIO 取纯文本。
    buf = io.StringIO()
    console = Console(
        file=buf, record=True, force_terminal=True, color_system="truecolor", width=80
    )
    return console, buf


def test_user_turn_uses_green_marker_and_echoes_text() -> None:
    console, _ = _console()
    render_user_turn(console, "帮我重构这个函数")

    text = console.export_text(styles=False)
    assert "›" in text  # 绿色前缀，呼应输入框
    assert "帮我重构这个函数" in text  # 原样回显用户输入


def test_assistant_turn_uses_teal_marker() -> None:
    console, _ = _console()
    render_assistant_turn(console, "好的，这是建议。")

    plain = console.export_text(styles=False)
    assert "●" in plain
    assert "好的，这是建议。" in plain


def test_user_input_is_not_interpreted_as_markup() -> None:
    # 用户输入含 "[...]" 不应被当成 Rich 样式标签吞掉，必须原样保留。
    console, _ = _console()
    render_user_turn(console, "see [bold]docs[/bold] here")

    text = console.export_text(styles=False)
    assert "[bold]docs[/bold]" in text


def test_assistant_multiline_hanging_indent() -> None:
    # 多行输出：首行接标记，后续行缩进 2 格对齐到文字列。
    console, _ = _console()
    render_assistant_turn(console, "第一行\n第二行")

    lines = console.export_text(styles=False).splitlines()
    assert lines[0].startswith("● 第一行")
    assert lines[1].startswith("  第二行")
