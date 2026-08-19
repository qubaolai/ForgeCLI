"""输入框的换行与自动折行.

**Shift+Enter 这个需求有一半不在代码这一侧.** 终端未必送得出它:

| 终端 | Shift+Enter 实际发什么 |
| --- | --- |
| kitty / WezTerm / Ghostty | CSI-u `\\x1b[13;2u`, 与 Enter 可区分 |
| macOS Terminal.app | 就是 `\\r`, 与 Enter 完全相同 |
| iTerm2 / VS Code / Windows Terminal | 默认同 Enter, 可手动配 |

所以实现挂了三个键. 这组用例逐个喂真实字节序列, 钉住的是"这几种输入方式都能换行", 而不是
某一个按键名 —— 按键名会随终端配置变, 字节序列不会.
"""

from __future__ import annotations

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth

from forgecli.interfaces.cli.prompt_loop import (
    _HINT_GROUPS,
    ForgePrompt,
    _fit_hint,
)

_ENTER = "\r"
_CTRL_J = "\x0a"
_SHIFT_ENTER_CSI_U = "\x1b[13;2u"
_ALT_ENTER = "\x1b\r"


def _read(feed: str) -> str:
    with (
        create_pipe_input() as pipe,
        create_app_session(input=pipe, output=DummyOutput()),
    ):
        prompt = ForgePrompt([("help", "帮助")], on_mode_step=None)
        pipe.send_text(feed)
        return prompt.read()


def test_plain_enter_still_submits() -> None:
    """回车提交是这个输入框最基本的语义, 加换行不能动它."""
    assert _read(f"abc{_ENTER}") == "abc"


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("Ctrl+J", _CTRL_J),
        ("Shift+Enter (CSI-u)", _SHIFT_ENTER_CSI_U),
        ("Alt+Enter", _ALT_ENTER),
    ],
)
def test_each_newline_key_inserts_a_newline(name: str, key: str) -> None:
    assert _read(f"ab{key}cd{_ENTER}") == "ab\ncd", name


def test_several_newlines_in_one_input() -> None:
    assert _read(f"a{_CTRL_J}b{_CTRL_J}c{_ENTER}") == "a\nb\nc"


def test_a_trailing_newline_survives() -> None:
    """换完行直接回车: 提交的是带尾换行的原文, 不该被悄悄裁掉."""
    assert _read(f"a{_CTRL_J}{_ENTER}") == "a\n"


def test_a_newline_closes_the_command_menu() -> None:
    """菜单开着时换行要先关掉它.

    留着的话下一次回车会去选菜单项而不是提交, 而用户此刻的意图明显是继续写.
    """
    assert _read(f"/he{_CTRL_J}后面还有{_ENTER}") == "/he\n后面还有"


def test_the_hint_mentions_a_key_that_always_arrives() -> None:
    """提示里写 Ctrl+J 而不是 Shift+↵.

    后者在一部分终端上根本送不到进程 —— 写上去等于让用户去按一个不生效的键.
    """
    prompt = ForgePrompt([("help", "帮助")], on_mode_step=None)
    hint = "".join(text for _, text in prompt._bottom_hint())  # noqa: SLF001

    assert "Ctrl+J" in hint
    assert "换行" in hint


# ---- 提示行按宽度自适应 ----
#
# 加了"换行"那一项之后, 提示行在 80 列终端上会和右侧状态撞在一起, 渲染成
# "Ctrl+C模式 accept_edits" 这种两段文字咬住的样子. 这是原本就有的毛病 —— 70 列时
# 已经会撞, 只是新增一项把它推到了 80 列这个最常见的宽度上.


def _hint_text(budget: int) -> str:
    return "".join(part[1] for part in _fit_hint(_HINT_GROUPS, budget))


def test_a_wide_terminal_shows_everything() -> None:
    text = _hint_text(90)

    for key, _ in _HINT_GROUPS:
        assert key in text


def test_a_narrow_terminal_drops_from_the_right() -> None:
    """按重要性从右往左丢: 少一个提示, 好过两段文字咬在一起."""
    text = _hint_text(34)

    assert "/" in text
    assert "发送" in text
    assert "退出" not in text


def test_a_dropped_group_goes_whole() -> None:
    """整组丢而不是截断文字 —— 半个 "Ctrl+C×" 比没有更让人困惑."""
    text = _hint_text(40)

    assert "Ctrl+C" not in text
    assert text.rstrip().endswith("换行")


def test_it_never_exceeds_its_budget() -> None:
    for budget in range(6, 60):
        assert get_cwidth(_hint_text(budget)) <= budget, budget
