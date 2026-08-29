"""工具入参进事件与上屏时的两次处理.

分两层是有原因的: 事件要**如实**带着模型传了什么, 展示端各自负责排版. 把排版提前到
事件里, 网页就再也拿不回原文了.

真实故障: 清理函数把所有控制字符一并剥掉, `\\n` 也在内. 于是 fs_create_file 的 content
从一段函数体变成 `def hi():    return 1` —— 看起来像一行合法代码, 实际上结构被抹掉了,
用户据此批准的东西和真正要写进文件的东西不是一回事.
"""

from __future__ import annotations

from forgecli.application.agent_run.scrubbing import scrub_arguments, scrub_text


def test_newlines_survive_into_the_event() -> None:
    body = "def hi():\n    return 1\n"

    assert scrub_arguments({"content": body}) == (("content", body),)


def test_escape_sequences_that_could_repaint_the_screen_are_stripped() -> None:
    """剥控制字符不是隐藏内容, 是防止参数里的 ANSI/OSC 序列重画终端."""
    assert scrub_text("\x1b[2Jrm -rf /\x07") == "[2Jrm -rf /"


def test_arguments_are_sorted_so_the_same_call_always_looks_the_same() -> None:
    assert scrub_arguments({"path": "a.py", "content": "x"}) == (
        ("content", "x"),
        ("path", "a.py"),
    )
