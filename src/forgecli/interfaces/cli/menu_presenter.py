"""MenuPresenter 的 Rich 实现: 单个 Live + 菜单栈, 全键盘导航

按键约定:
    ↑/↓        移动高亮
    Enter      导航行下钻；文本行进入行内编辑；动作行触发动作
    ←/→        仅用于开关/枚举行原地切换(右=下一个，左=上一个)
    /          进入搜索, 输入即过滤本层; Enter 保留过滤, Esc 清除
    Esc 子菜单回上一层；根层关闭命令
    Ctrl-C 任意状态 / 任意层级直接关闭整个菜单

菜单栈保存的是"构建器"(Callable[[], Menu])而非静态 Menu：每次渲染重建当前层，
因此增删项(如新增/删除模型)后列表会就地刷新，与 preview 的"每次渲染重读"一致。
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from rich.cells import cell_len
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from forgecli.application.interaction_ports import MenuPresenter
from forgecli.application.menu import Choice, Menu
from forgecli.interfaces.cli.tty.tty import Key, raw_mode, read_key, stdin_is_tty

# 配色与 prompt_loop 的「裸斜杠菜单」保持一致：背景透明、选中仅靠文字颜色区分
# （不反显、不铺底色），边框用灰色与输入框同色。
_FRAME = "bright_black"  # 面板边框（同输入框）
_PROMPT = "bold green"  # 行内编辑 / 搜索的 "›" 前缀，呼应输入行
_HINT_KEY = "cyan"  # 提示行里的按键
_HINT_DIM = "bright_black"  # 提示行里的说明文字
_NAME = "#9399b2"  # 普通行：标签
_META = "#6c7086"  # 普通行：右侧取值
_NAME_CUR = "#94e2d5"  # 选中行：标签（青绿）
_META_CUR = "#cdd6f4"  # 选中行：右侧取值（提亮）

# 普通态提示行的按键序列（键, 说明）。
_NAV_HINTS = (
    ("↑↓", "选择"),
    ("Enter", "进入/编辑"),
    ("←→", "切换"),
    ("/", "搜索"),
    ("Esc", "返回"),
)


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

        # 栈里放构建器；每轮渲染重建当前层，增删项后就地刷新。
        stack: list[Callable[[], Menu]] = [lambda: root]
        index = 0
        query = ""
        searching = False
        previewing = False
        editing: tuple[Choice, str] | None = None
        fd = sys.stdin.fileno()

        with (
            raw_mode(fd=fd),
            Live(console=self._console, auto_refresh=False, screen=False) as live,
        ):
            while stack:
                menu = stack[-1]()
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
                        editing=editing,
                        previewing=previewing,
                    ),
                    refresh=True,
                )
                press = read_key(fd=fd)

                # Ctrl-C 在任意状态 / 任意层级都直接关闭整个菜单。
                if press.key is Key.CTRL_C:
                    break

                if editing is not None:  # 行内文本编辑
                    choice, buf = editing
                    if press.key is Key.ENTER:
                        if choice.on_text:
                            choice.on_text(buf)
                        editing = None
                    elif press.key is Key.ESC:
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

                if press.key is Key.ESC:
                    if len(stack) > 1:
                        stack.pop()  # Esc 逐层返回
                        index, query = 0, ""
                    else:
                        break  # 根层 Esc 关闭命令
                    continue
                if press.key is Key.SLASH:
                    searching, query = True, ""
                    continue
                # 空格切换「摘要预览」：当前行有 summary 时在列表下方展开/收起其摘要。
                if press.key is Key.CHAR and press.char == " ":
                    previewing = not previewing
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
                elif press.key is Key.ENTER:
                    # Enter 只表示「确认 / 下钻」：进入子菜单、进入行内编辑、触发动作。
                    # 开关 / 枚举行的切换只走 ←/→，Enter 在这类行上不切换候选值。
                    if row.submenu:
                        stack.append(row.submenu)
                        index, query = 0, ""
                    elif row.on_text is not None:
                        editing = (row, row.text_default() if row.text_default else "")
                    elif row.on_select is not None:
                        row.on_select()
                        # 选择即关闭语义（如 /resume 选中会话后退出菜单去恢复）。
                        if row.close_on_select:
                            break

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
        editing: tuple[Choice, str] | None,
        previewing: bool = False,
    ) -> Panel:
        # 标签列定宽对齐（按显示宽度，兼容中日韩全角），右侧接取值。
        label_width = max((cell_len(row.label) for row in rows), default=0) + 2
        lines: list[Text] = []
        for i, row in enumerate(rows):
            current = i == index
            name_style = _NAME_CUR if current else _NAME
            meta_style = _META_CUR if current else _META
            line = Text("  ")  # 两格缩进，呼应裸斜杠菜单
            pad = " " * (label_width - cell_len(row.label))
            line.append(row.label + pad, style=name_style)
            preview = row.preview() if row.preview else ""
            if preview:
                shown = f"◀ {preview} ▶" if row.on_cycle else preview
                line.append(shown, style=meta_style)
            lines.append(line)

        if not rows:
            lines.append(Text("  （无匹配项）", style=_META))

        if editing is not None:
            choice, buf = editing
            tail = Text("› ", style=_PROMPT)
            tail.append(f"{choice.label}: ", style=_NAME)
            tail.append(buf + "▌", style=_NAME_CUR)
            body = Group(*lines, Text(""), tail)
            hint = self._hint(("Enter", "确认"), ("Esc", "取消"))
        elif searching:
            tail = Text("› ", style=_PROMPT)
            tail.append("搜索 ", style=_NAME)
            tail.append(query + "▌", style=_NAME_CUR)
            body = Group(*lines, Text(""), tail)
            hint = self._hint(("输入", "过滤"), ("Enter", "确定"), ("Esc", "清除"))
        else:
            current_row = rows[index] if rows else None
            previewable = any(row.payload for row in rows)
            renderables: list[Text] = list(lines)
            if previewing and current_row is not None and current_row.payload:
                renderables.append(Text(""))
                renderables.extend(self._preview_lines(current_row.payload()))
            body = Group(*renderables)
            hints = list(_NAV_HINTS)
            if previewable:
                # 在「Enter」之后插入空格预览提示，仅当本层有可预览行时出现。
                hints.insert(2, ("Space", "预览"))
            hint = self._hint(*hints)
        return Panel(
            body,
            title=menu.title,
            title_align="left",
            subtitle=hint,
            subtitle_align="left",
            border_style=_FRAME,
        )

    @staticmethod
    def _preview_lines(summary: str) -> list[Text]:
        """把当前行的多行摘要渲染成列表下方的缩进预览块（青绿标题 + 灰正文）。"""
        lines = [Text("  摘要预览", style=_NAME_CUR)]
        for raw in summary.split("\n"):
            lines.append(Text(f"    {raw}", style=_META_CUR))
        return lines

    @staticmethod
    def _hint(*segments: tuple[str, str]) -> Text:
        """提示行：按键用青色、说明用灰色，与裸斜杠菜单的底部提示一致。"""
        text = Text()
        for i, (key, label) in enumerate(segments):
            if i:
                text.append("  ·  ", style=_HINT_DIM)
            text.append(key, style=_HINT_KEY)
            text.append(f" {label}", style=_HINT_DIM)
        return text
