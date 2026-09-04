"""输入框的判据: 斜杠菜单该在什么时候出现, 提示行放不下时怎么退让.

按键与渲染本身交给 prompt-toolkit, 这里不重测它. 测的是我们自己那几条规则 —— 它们
坏掉的表现都是"某个键按了没反应"或"菜单在不该出现的时候出现", 没有任何东西会报错.
"""

from __future__ import annotations

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from forgecli.interfaces.tui.prompt import (
    _HINT_GROUPS,
    PromptCommand,
    _fit_hint,
    _SlashCompleter,
)

_COMMANDS = (
    ("mode", "隔离档与审批档"),
    ("model", "当前模型, 用途覆盖, 模型与供应商配置"),
    ("status", "会话, 模式, 模型与目录的当前状态"),
)


def _completions(text: str) -> list[tuple[str, str]]:
    completer = _SlashCompleter(_COMMANDS)
    document = Document(text, len(text))
    return [
        (item.display_text, item.display_meta_text)
        for item in completer.get_completions(document, CompleteEvent())
    ]


def test_slash_opens_the_menu_with_every_command() -> None:
    assert [name for name, _ in _completions("/")] == ["/mode", "/model", "/status"]


def test_menu_narrows_as_you_type() -> None:
    assert [name for name, _ in _completions("/mod")] == ["/mode", "/model"]


def test_menu_can_search_chinese_summary_when_name_has_no_prefix() -> None:
    commands = (
        PromptCommand("config", "配置值与来源", "设置", ("日志",)),
        PromptCommand("status", "当前状态", "会话"),
    )
    completer = _SlashCompleter(commands)
    document = Document("/日志", 3)
    found = [item.text for item in completer.get_completions(document, CompleteEvent())]
    assert found == ["config"]


def test_menu_carries_the_one_line_summary() -> None:
    """光有命令名不够: 用户记不住 /gateway 和 /config 的分工,
    而菜单是他唯一能看到说明的地方.
    """
    assert _completions("/mode")[0] == ("/mode", "隔离档与审批档")


def test_plain_text_does_not_open_the_menu() -> None:
    """自然语言是这个输入框的主要用途, 不该被补全打扰."""
    assert _completions("帮我看看这个 bug") == []


def test_arguments_stop_the_command_completion() -> None:
    """命令名后出现空格就是在写参数了; 继续补命令名会把参数替换掉."""
    assert _completions("/mode auto") == []


def test_replacement_span_covers_what_was_typed() -> None:
    """start_position 是负的: 选中后要变成完整的 /mode, 而不是把 mode 接在 /mod 后面."""
    completer = _SlashCompleter(_COMMANDS)
    document = Document("/mod", 4)
    first = next(iter(completer.get_completions(document, CompleteEvent())))
    assert first.start_position == -3
    assert first.text == "mode"


def test_hint_drops_whole_groups_when_narrow() -> None:
    """窄终端上按重要性从右往左丢, 整组丢而不是截断: 半个 Ctrl+C× 比没有更让人困惑."""
    wide = "".join(str(part[1]) for part in _fit_hint(_HINT_GROUPS, 200))
    narrow = "".join(str(part[1]) for part in _fit_hint(_HINT_GROUPS, 20))
    assert "Ctrl+C×2" in wide
    assert "/" in narrow
    assert "Ctrl+C×2" not in narrow
    assert "Ctrl+C×" not in narrow


def test_submitting_records_history() -> None:
    """↑ 要翻得到自己刚说过的话.

    平时 append_to_history 是 validate_and_handle() 顺手做的, 而这个输入框直接
    app.exit(result=...) 绕过了它 —— 不显式补一句, 历史永远是空的, 而且不会报错.
    """
    from prompt_toolkit.history import InMemoryHistory

    from forgecli.interfaces.tui.prompt import ForgePrompt

    prompt = ForgePrompt(_COMMANDS, on_mode_step=lambda _step: None)
    history = InMemoryHistory()
    prompt._buffer.history = history
    prompt._buffer.text = "跑一下测试"
    prompt._buffer.append_to_history()
    assert list(history.get_strings()) == ["跑一下测试"]


def test_history_falls_back_to_memory_without_a_file() -> None:
    """拿不到磁盘只是少一次跨会话的便利, 不该让输入框起不来."""
    from prompt_toolkit.history import InMemoryHistory

    from forgecli.interfaces.tui.prompt import _history

    assert isinstance(_history(None), InMemoryHistory)


def test_menu_window_keeps_the_highlight_visible() -> None:
    """截断成"只显示前 N 条"的那一版让 /clear 在菜单里根本不存在.

    而用户看到的是一张看起来完整的表, 没有任何东西提示它下面还有.
    """
    from forgecli.interfaces.tui.prompt import _menu_window

    assert _menu_window(total=21, selected=0, visible=10) == 0
    assert _menu_window(total=21, selected=19, visible=10) == 11
    assert _menu_window(total=21, selected=10, visible=10) == 5
    assert _menu_window(total=5, selected=4, visible=10) == 0
