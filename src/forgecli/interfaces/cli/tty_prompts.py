"""两个轻量 TTY 交互组件：首启信任确认、工作区目录输入。

与 menu_presenter 同套底层：KeyReader + rich.Live 就地重绘；配色一致。
非 TTY（管道 / CI / 测试）直接返回安全默认，绝不阻塞——交互结果在测试里用 fake 注入。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from forgecli.application.interaction_ports import DirectoryPicker, TrustPrompter
from forgecli.interfaces.cli.tty.tty import (
    BACKSPACE_KEYS,
    CONFIRM_KEYS,
    KeyReader,
    Keys,
    is_text,
    stdin_is_tty,
)

_FRAME = "bright_black"
_PROMPT = "bold green"
_NAME = "#9399b2"
_NAME_CUR = "#94e2d5"
_HINT_KEY = "cyan"
_HINT_DIM = "bright_black"


def _hint(*segments: tuple[str, str]) -> Text:
    text = Text()
    for i, (key, label) in enumerate(segments):
        if i:
            text.append("  ·  ", style=_HINT_DIM)
        text.append(key, style=_HINT_KEY)
        text.append(f" {label}", style=_HINT_DIM)
    return text


class TtyTrustPrompter(TrustPrompter):
    """信任确认：``[是] [否]`` 横向选择，默认高亮 [是]。

    ←/→ 切换，Enter 确认当前项，y/n 直选，Esc / Ctrl-C 视为否。
    """

    def __init__(self, console: Console) -> None:
        self._console = console

    def confirm(self, path: str) -> bool:
        if not stdin_is_tty():
            return False
        yes = True  # 默认高亮 [是]
        with (
            KeyReader() as keys,
            Live(console=self._console, auto_refresh=False, screen=False) as live,
        ):
            while True:
                live.update(self._render(path, yes), refresh=True)
                press = keys.read()
                if press.key in (Keys.ControlC, Keys.Escape):
                    return False
                if press.key is Keys.Left:
                    yes = True
                elif press.key is Keys.Right:
                    yes = False
                elif press.key in CONFIRM_KEYS:
                    return yes
                elif is_text(press):
                    low = press.data.lower()
                    if low == "y":
                        return True
                    if low == "n":
                        return False

    def _render(self, path: str, yes: bool) -> Panel:
        question = Text("是否信任当前目录？", style=_NAME)
        location = Text(path, style=_NAME_CUR)
        options = Text("  ")
        options.append("[是]", style=_NAME_CUR if yes else _NAME)
        options.append("   ")
        options.append("[否]", style=_NAME if yes else _NAME_CUR)
        body = Group(question, location, Text(""), options)
        return Panel(
            body,
            title="目录信任",
            title_align="left",
            subtitle=_hint(("←→", "选择"), ("Enter", "确认"), ("y/n", "直选")),
            subtitle_align="left",
            border_style=_FRAME,
        )


class TtyDirectoryPicker(DirectoryPicker):
    """目录输入：一个输入框 + 当前目录子目录参考列表（只读，不可选中）。

    打字编辑，Enter 提交（空提交视为取消），Esc / Ctrl-C 取消。
    """

    def __init__(self, console: Console) -> None:
        self._console = console

    def pick(self, list_subdirs: Callable[[str], Sequence[str]]) -> str | None:
        if not stdin_is_tty():
            return None
        buf = ""
        listed_for = ""  # 当前列出的是哪个输入路径的子目录（空=当前目录）
        subdirs = list(list_subdirs(""))
        with (
            KeyReader() as keys,
            Live(console=self._console, auto_refresh=False, screen=False) as live,
        ):
            while True:
                live.update(self._render(buf, subdirs, listed_for), refresh=True)
                press = keys.read()
                if press.key in (Keys.ControlC, Keys.Escape):
                    return None
                if press.key in CONFIRM_KEYS:
                    return buf if buf.strip() else None
                if press.key is Keys.ControlI:
                    # 按 Tab 列出当前输入路径下的子目录（空输入则列当前目录）。
                    subdirs = list(list_subdirs(buf))
                    listed_for = buf.strip()
                elif press.key in BACKSPACE_KEYS:
                    buf = buf[:-1]
                elif is_text(press):
                    buf += press.data

    def _render(self, buf: str, subdirs: Sequence[str], listed_for: str) -> Panel:
        lines: list[Text] = []
        entry = Text("› ", style=_PROMPT)
        entry.append("输入目录路径: ", style=_NAME)
        entry.append(buf + "▌", style=_NAME_CUR)
        lines.append(entry)
        lines.append(Text(""))
        header = f"“{listed_for}” 下的子目录:" if listed_for else "当前目录下的子目录:"
        lines.append(Text(header, style=_HINT_DIM))
        if subdirs:
            lines.extend(Text(f"- {name}", style=_NAME) for name in subdirs)
        else:
            lines.append(Text("（无）", style=_HINT_DIM))
        return Panel(
            Group(*lines),
            title="添加可操作目录",
            title_align="left",
            subtitle=_hint(("Enter", "确认"), ("Tab", "列出子目录"), ("Esc", "取消")),
            subtitle_align="left",
            border_style=_FRAME,
        )
