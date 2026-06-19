"""MenuPresenter 的 Rich 实现: 单个 Live + 菜单栈, 全键盘导航

按键约定:
    ↑/↓        移动高亮
    →/Enter    导航行下钻；文本行进入行内编辑
    ←/→        开关/枚举行原地切换(右=下一个，左=上一个)
    /          进入搜索, 输入即过滤本层; Enter 保留过滤, Esc 清除
    Esc 子菜单回上一层；根层关闭命令
"""

from __future__ import annotations
import sys

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from forgecli.application.menu import Choice, Menu
from forgecli.application.ports import MenuPresenter
from forgecli.interfaces.cli.tty import Key, raw_mode, read_key, stdin_is_tty


class RichMenuPresenter(MenuPresenter):
    def __init__(self, console: Console) -> None:
        self._console = console

    def present(self, menu: Menu) -> None:
        # 只支持方向键交互。非 TTY(管道 / CI / 测试)不降级编号，直接拒绝并退出，
        # 避免 raw 模式在非终端 fd 上抛 termios.error。
        if not stdin_is_tty():
            self._console.print("[yellow]交互式配置需要在终端(TTY)中运行。[/]")
            return
        self._run_tty(menu)

    def _run_tty(self, root: Menu) -> None:
        from rich.live import Live

        stack: list[Menu] = [root]
        index = 0
        query = ""
        searching = False
        editing: tuple[Choice, str] | None = None
        fd = sys.stdin.fileno()

        with raw_mode(fd=fd), Live(
            console=self._console, auto_refresh=False, screen=False
        ) as live:
            while stack:
                menu = stack[-1]
                rows = self._visible(menu=menu, query=query)
                index = min(index, len(rows) - 1) if rows else 0
                index = max(index, 0)
                live.update(
                    self._redner(
                        menu=menu,
                        rows=rows,
                        index=index,
                        searching=searching,
                        query=query,
                        editing=editing
                    ),
                    refresh=True,
                )
                press = read_key(fd=fd)

                if editing is not None:  # 行内文本编辑
                    choice, buf = editing
                    if press.key is Key.ENTER:
                        if choice.on_text:
                            choice.on_text(buf)
                        editing = None
                    elif press.key in (Key.ESC, Key.CTRL_C):
                        editing = None
                    elif press.key is Key.BACKSPACE:
                        editing = (choice, buf[:-1])
                    elif press.key in (Key.CHAR, Key.SLASH):
                        editing = (choice, buf + press.char)
                    continue

                if searching:  # 搜索输入
                    if press.key is Key.ENTER:
                        searching = False
                    elif press.key is Key.ESC:
                        searching, query = False, ""
                    elif press.key is Key.BACKSPACE:
                        query = query[:-1]
                    elif press.key in (Key.CHAR, Key.SLASH):
                        query += press.char
                    index = 0
                    continue

                if press.key in (Key.ESC, Key.CTRL_C):
                    if len(stack) > 1:
                        stack.pop()
                        index, query = 0, ""
                    else:
                        break  # 根层 Esc 关闭命令
                    continue
                if press.key is Key.SLASH:
                    searching, query = True, ""
                    continue
                if not rows:
                    continue

                row = rows[index]
                if press.key is Key.UP:
                    index = (index - 1) % len(rows)
                elif press.key is Key.DOWN:
                    index = (index + 1) % len(rows)
                elif press.key is Key.LEFT:
                    if row.on_cycle:
                        row.on_cycle(-1)
                elif press.key is Key.RIGHT:
                    if row.on_cycle:
                        row.on_cycle(+1)
                    elif row.submenu:
                        stack.append(row.submenu())
                        index, query = 0, ""
                    elif row.on_text is not None:
                        editing = (row, row.text_default() if row.text_default else "")
                elif press.key is Key.ENTER:
                    if row.submenu:
                        stack.append(row.submenu())
                        index, query = 0, ""
                    elif row.on_text is not None:
                        editing = (row, row.text_default() if row.text_default else "")
                    elif row.on_cycle:
                        row.on_cycle(+1)



    def _visible(self, menu: Menu, query: str) -> list[Choice]:
        if not query:
            return list(menu.choices)
        
        q = query.lower()
        return [c for c in menu.choices if q in c.label.lower()]
    
    def _redner(
        self,
        menu: Menu,
        rows: list[Choice],
        index: int,
        searching: bool,
        query: str,
        editing: tuple[Choice, str] | None
    ) -> Panel:
        lines: list[Text] = []
        for i, row in enumerate(rows):
            highlight = i == index
            line = Text("> " if highlight else " ", style="bold cyan")
            line.append(row.label, style="reverse" if highlight else "")
            preview = row.preview() if row.preview else ""
            if preview:
                line.append("    ")
                if row.on_cycle:
                    line.append(f"◀ {preview} ▶", style="yellow")
                else:
                    line.append(preview, style="dim")

            lines.append(line)

        if not rows:
            lines.append(Text("（无匹配项）", style="dim"))

        if editing is not None:
            choice, buf = editing
            tail = Text(f"编辑 {choice.label}: ", style="bold")
            tail.append(buf + "▌")
            hint = "Enter 确认 · Esc 取消"
            body = Group(*lines, Text(""), tail)
        elif searching:
            tail = Text("搜索 /", style="bold")
            tail.append(query + "▌")
            hint = "输入过滤 · Enter 确定 · Esc 清除"
            body = Group(*lines, Text(""), tail)
        else:
            hint = "↑↓ 选择 · → 进入/切换 · ← 切换 · / 搜索 · Esc 返回"
            body = Group(*lines)
        return Panel(body, title=menu.title, subtitle=hint, border_style="cyan")