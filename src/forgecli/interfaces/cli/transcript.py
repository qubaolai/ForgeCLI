"""会话记录(transcript)渲染：把用户输入与助手输出分色显示。

为什么需要：输入框提交后会被擦除(prompt_loop 的 erase_when_done)，用户那一句不会
留在滚动历史里。这里在提交后把「用户那一轮 + 助手回复」重新打印进历史，并用颜色
把二者清晰区分开。

配色沿用全局方案(与 menu_presenter / prompt_loop 同源，均为 Catppuccin 系)：
    - 用户：绿色 "›" 前缀，呼应输入框的绿色提示符；正文用偏暗的次要色(它是回显)。
    - 助手：青绿 "●" 标记(= 菜单选中色 #94e2d5)，与用户的绿色区分；正文用提亮主色。
标记同宽(1 字符 + 空格)，多行正文按 2 格悬挂缩进，对齐到文字列。

用户输入一律走 Text.append(literal)，不解释 Rich 标记——避免用户输入里的 "[...]"
被当成样式标签注入。
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

_USER_MARK = "bold green"  # 用户行标记 "›"：呼应输入框前缀
_USER_TEXT = "#9399b2"  # 用户输入正文：次要色(回显，弱化)
_ASSISTANT_MARK = "bold #94e2d5"  # 助手行标记 "●"：青绿，菜单选中同源色
_ASSISTANT_TEXT = "#cdd6f4"  # 助手输出正文：提亮主色(对话焦点)

_THINKING = "处理中..."
_SPINNER_STYLE = "#94e2d5"

_NOTICE_TEXT = "#6c7086"  # 收尾提示(取消/失败)：灰色，弱化——非模型正文

_MARK = "●"
_INDENT = "  "  # 续行悬挂缩进：对齐 "● " 之后的文字列(标记同宽)


def render_user_turn(console: Console, text: str) -> None:
    """回显用户这一轮输入(绿色 "›" 前缀)。"""
    console.print(_turn("›", _USER_MARK, text, _USER_TEXT))


def render_assistant_turn(console: Console, text: str) -> None:
    """打印助手这一轮输出(青绿 "●" 标记)，末尾留一空行分隔下一轮。"""
    console.print(assistant_text(text))
    console.print()


def assistant_text(text: str) -> Text:
    """助手样式正文（"●" 标记 + 悬挂缩进）；整块打印用（非流式回退 / 正常轮收尾）。"""
    return _turn(_MARK, _ASSISTANT_MARK, text, _ASSISTANT_TEXT)


def assistant_line(text: str, *, first: bool) -> Text:
    """流式逐行提交的一行(单行，不含换行)：全局首行带 "●" 标记，续行悬挂缩进。"""
    body = Text()
    if first:
        body.append(f"{_MARK} ", style=_ASSISTANT_MARK)
    else:
        body.append(_INDENT)
    body.append(text, style=_ASSISTANT_TEXT)
    return body


def assistant_notice(text: str) -> Text:
    """收尾提示(取消 / 失败说明)：灰色，悬挂缩进对齐正文列，无 "●" 标记。"""
    body = Text()
    for i, line in enumerate(text.split("\n")):
        if i:
            body.append("\n")
        body.append(_INDENT)
        body.append(line, style=_NOTICE_TEXT)
    return body


def _turn(mark: str, mark_style: str, text: str, text_style: str) -> Text:
    """标记 + 正文；多行正文按 2 格悬挂缩进对齐到文字列。"""
    body = Text()
    body.append(f"{mark} ", style=mark_style)
    for i, line in enumerate(text.split("\n")):
        if i:
            body.append("\n  ")
        body.append(line, style=text_style)
    return body
