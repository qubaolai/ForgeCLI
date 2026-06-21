"""基于 prompt_toolkit 的交互式输入，仿照 Claude Code 的输入框样式与退出逻辑。

为什么用完整的 Application 而不是 PromptSession.prompt()：
    prompt() 只能渲染一个"前缀 + 输入"的单行，画不出输入框的上下边框，也放不下
    一个整行宽的命令菜单。要做出 Claude Code 那种界面，必须自己搭 prompt_toolkit
    的布局(Layout)，所以这里直接用底层的 Application。

    代价是：PromptSession 自带的方向键/退格/补全等默认按键，在裸 Application 里
    需要手动挂上——用 load_key_bindings() 一次性加载，再 merge 上我们自己的绑定。

界面结构（从上到下）：
      /init     初始化项目        ← 斜杠命令菜单（输入 "/" 时出现，全宽两列）
      /help     查看帮助
    ╭─────────────────────╮   ← 上边框
    │ › 用户在这里输入       │   ← 输入行（固定一行高，贴在底部）
    ╰─────────────────────╯   ← 下边框
      / 命令  ↵ 发送  Ctrl+C×2 退出   ← 框下提示行
    输入框始终在底部，上方的终端区域留给对话历史显示（终端原生滚动回看）。

斜杠命令菜单（不同于内置浮动下拉，是自绘的全宽面板）：
    - 输入 "/" 即出现，列出匹配命令，命令名与说明分两列对齐、铺满整行；
    - ↑/↓ 移动高亮，Tab 同样可切换；
    - Enter 选中（填入输入行）；命令已完整时 Enter 直接提交；
    - Esc 关闭菜单。

退出逻辑（仿 Claude Code 的双击 Ctrl-C）：
    - 输入框有内容时按 Ctrl-C → 清空内容（不退出）；
    - 空行第一次 Ctrl-C → 提示行变为"再按一次 Ctrl-C 退出"，并起一个 500ms 定时器；
    - 空行在 500ms 内再次 Ctrl-C → 抛出 QuitSignal 退出；
    - 超过 500ms 没有第二次 → 定时器复位"待退出"并重绘，提示行还原（第二次需重新计时）；
    - 空行 Ctrl-D → 抛出 EOFError 退出；
    - 一旦开始打字，"待退出"状态立即解除。

只在真终端(TTY)里用本模块；非 TTY 由 repl 直接拒绝。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.filters import has_completions
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.defaults import load_key_bindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    HSplit,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.styles import Style

# 命令菜单最多显示的行数；命令很多时只显示前若干行（当前命令数远小于它）。
_MENU_MAX_ROWS = 12

# 两次 Ctrl-C 退出的有效间隔（秒）。超过它，第一次按键作废，需重新计时。
_EXIT_WINDOW = 0.5

# 所有界面元素的配色集中在这里，方便统一改主题。
# "class:xxx" 在布局里被引用，这里给出每个 class 对应的样式字符串。
_STYLE = Style.from_dict(
    {
        "frame": "ansibrightblack",  # 输入框边框：灰色
        "prompt": "bold ansigreen",  # 输入行前缀 "› "
        "hint": "ansicyan",  # 提示行里的按键，如 "/"、"↵"
        "hint-dim": "ansibrightblack",  # 提示行里的说明文字
        "hint-alert": "bold ansiyellow",  # "再按一次 Ctrl-C 退出" 警示
        # 斜杠命令菜单（背景透明：不设 bg，终端底色透出来）。
        # 选中项仅靠文字颜色区分：普通行偏暗，选中行用青绿色 + 提亮的说明。
        "menu-name": "#9399b2",  # 普通行：命令名（偏暗）
        "menu-meta": "#6c7086",  # 普通行：说明（更暗）
        "menu-name-current": "#94e2d5",  # 选中行：命令名（青绿）
        "menu-meta-current": "#cdd6f4",  # 选中行：说明（提亮）
    }
)


class QuitSignal(Exception):
    """用户在空行连按两次 Ctrl-C，要求退出会话。

    用"抛异常"而非返回特殊字符串来表达退出，能和正常的一行输入彻底区分开：
    调用方 except 捕获它即可，不会和用户真的输入了某段文本混淆。
    """


class _SlashCompleter(Completer):
    """斜杠命令补全器：当输入以 "/" 开头时，列出匹配的已注册命令。

    prompt_toolkit 的补全机制会在每次输入变化时调用 get_completions，
    我们据此返回候选项；候选项由 ForgePrompt 自绘成全宽菜单。
    """

    def __init__(self, commands: Sequence[tuple[str, str]]) -> None:
        # commands 是 (命令名, 一句话说明) 的列表，来自 CommandRegistry。
        self._commands = commands

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        # 不是斜杠命令，不补全（普通自然语言输入不打扰）。
        if not text.startswith("/"):
            return
        word = text[1:]  # 去掉前导 "/"，得到已输入的命令名部分
        # 命令名后一旦出现空格，说明在写参数了，不再补全命令名。
        if " " in word:
            return
        for name, summary in self._commands:
            if name.startswith(word):
                yield Completion(
                    name,
                    # start_position 是负数：从光标往前替换掉已输入的 word 部分，
                    # 这样选中后变成完整的 "/name"，而不是把 name 又接在后面。
                    start_position=-len(word),
                    display=f"/{name}",  # 菜单里显示的命令名
                    display_meta=summary,  # 菜单里显示的说明
                )


class ForgePrompt:
    """可重复调用的输入提示器：每次 read() 弹出一个带边框的输入框并返回一行输入。"""

    def __init__(self, commands: Sequence[tuple[str, str]]) -> None:
        # "待退出"标志：空行第一次 Ctrl-C 后置 True；它决定提示行是否显示退出警示。
        self._exit_armed = False
        # 待退出复位定时器：第一次 Ctrl-C 起，500ms 内没有第二次就把它复位。
        self._reset_handle: asyncio.TimerHandle | None = None

        # 输入缓冲区：挂上斜杠补全器，并开启"边打字边补全"。
        self._buffer = Buffer(
            completer=_SlashCompleter(commands),
            complete_while_typing=True,
            multiline=False,
        )
        # 用户一开始打字就解除"待退出", 输入会驱散退出提示。
        self._buffer.on_text_changed += self._on_text_changed

        self._app = self._build_app()

    def read(self) -> str:
        """弹出输入框，返回用户输入的一行。

        - 正常回车：返回该行文本；
        - 空行连按两次 Ctrl-C：抛出 QuitSignal；
        - 空行 Ctrl-D：抛出内置的 EOFError。
        调用方负责捕获这两个异常并据此退出。
        """
        self._disarm_exit()
        self._buffer.reset()  # 清掉上一轮可能残留的内容
        # app.run() 会一直运行直到某个按键调用了 app.exit(...)，
        # 返回值就是 exit(result=...) 里传的字符串。
        return self._app.run()

    # ----- 下面都是构建界面用的内部方法 -----

    def _on_text_changed(self, _buffer: Buffer) -> None:
        """输入内容一变化就解除待退出状态（打字驱散退出提示）。"""
        self._disarm_exit()

    def _arm_exit(self, app: Application[str]) -> None:
        """进入待退出，并起一个定时器：到点没有第二次 Ctrl-C 就复位。"""
        self._exit_armed = True
        if self._reset_handle is not None:
            self._reset_handle.cancel()
        self._reset_handle = asyncio.get_running_loop().call_later(
            _EXIT_WINDOW, lambda: self._reset_exit(app)
        )

    def _disarm_exit(self) -> None:
        """解除待退出，并撤掉可能在跑的复位定时器。"""
        self._exit_armed = False
        if self._reset_handle is not None:
            self._reset_handle.cancel()
            self._reset_handle = None

    def _reset_exit(self, app: Application[str]) -> None:
        """定时器到点：复位待退出并重绘，把"再按一次"提示还原。"""
        self._exit_armed = False
        self._reset_handle = None
        app.invalidate()

    def _prompt_prefix(self, line_number: int, wrap_count: int) -> StyleAndTextTuples:
        """输入行左侧的前缀。单行输入，固定显示 "› "。"""
        return [("class:prompt", "› ")]

    def _bottom_hint(self) -> StyleAndTextTuples:
        """框下提示行的内容，随"待退出"状态切换。"""
        if self._exit_armed:
            return [("class:hint-alert", "  再按一次 Ctrl-C 退出")]
        # 普通态：列出主要按键，仿照 Claude Code 的快捷键提示。
        return [
            ("class:hint-dim", "  "),
            ("class:hint", "/"),
            ("class:hint-dim", " 命令   "),
            ("class:hint", "↵"),
            ("class:hint-dim", " 发送   "),
            ("class:hint", "Ctrl+C×2"),
            ("class:hint-dim", " 退出"),
        ]

    def _render_menu(self) -> StyleAndTextTuples:
        """自绘斜杠命令菜单：每行 = 命令名(定宽) + 说明；选中项仅靠文字颜色区分。

        背景透明（不铺底色），故无需按终端宽度补齐整行。
        数据来自缓冲区的补全状态 complete_state：
            - .completions     当前匹配到的候选项列表
            - .complete_index  当前选中的下标（可能为 None，此时默认高亮第一行）
        """
        state = self._buffer.complete_state
        if state is None or not state.completions:
            return []
        completions = state.completions
        selected = state.complete_index if state.complete_index is not None else 0

        # 命令名列宽 = 最长的 "/name" 再留两格间距，保证说明列对齐。
        name_width = max(len(c.display_text) for c in completions) + 2

        fragments: StyleAndTextTuples = []
        for index, comp in enumerate(completions):
            current = "-current" if index == selected else ""
            if index:  # 行之间换行（最后一行不加，避免多出空行）
                fragments.append(("", "\n"))
            name = f"  {comp.display_text.ljust(name_width)}"
            fragments.append((f"class:menu-name{current}", name))
            fragments.append((f"class:menu-meta{current}", comp.display_meta_text))
        return fragments

    def _build_app(self) -> Application[str]:
        """把缓冲区、布局、按键、样式组装成一个可运行的 Application。"""
        # 输入区窗口：固定为一行高，左侧带 "› " 前缀。
        # 关键：height=1 + dont_extend_height=True，否则 Window 默认会纵向撑满终端，
        # 把输入框拉成很高的一块。内容过宽时横向滚动（multiline=False，不换行）。
        input_window = Window(
            BufferControl(buffer=self._buffer),
            get_line_prefix=self._prompt_prefix,
            wrap_lines=False,
            height=1,
            dont_extend_height=True,
        )

        # 斜杠命令菜单面板：放在输入框上方，仅当有补全候选时显示(has_completions)。
        # dont_extend_height 让它按内容行数自适应；Dimension(max=...) 给个上限。
        menu_pane = ConditionalContainer(
            content=Window(
                FormattedTextControl(self._render_menu),
                height=Dimension(min=1, max=_MENU_MAX_ROWS),
                dont_extend_height=True,
            ),
            filter=has_completions,
        )

        # 用 HSplit（纵向堆叠）拼出"菜单 / 上边框 / 输入行 / 下边框 / 提示行"。
        root = HSplit(
            [
                _border_row("╭", "╮"),
                # 中间这行用 VSplit（横向）：左竖线 + 输入区 + 右竖线。
                VSplit(
                    [
                        Window(width=1, char="│", style="class:frame"),
                        input_window,
                        Window(width=1, char="│", style="class:frame"),
                    ]
                ),
                _border_row("╰", "╯"),
                # 框下提示行：内容由 _bottom_hint 动态返回。
                Window(FormattedTextControl(self._bottom_hint), height=1),
                menu_pane,
            ]
        )

        return Application(
            layout=Layout(root, focused_element=input_window),
            # 默认按键（方向键/退格/补全等）+ 我们自定义的按键，后者优先。
            key_bindings=merge_key_bindings(
                [load_key_bindings(), self._key_bindings()]
            ),
            style=_STYLE,
            # 非全屏：渲染在当前光标处、保留上方的滚动历史（banner、过往输出）。
            full_screen=False,
            # 提交后擦掉输入框本身，让界面保持干净（输出由 REPL 另行打印）。
            erase_when_done=True,
            mouse_support=False,
        )

    def _key_bindings(self) -> KeyBindings:
        """自定义按键：菜单导航、回车提交、Ctrl-C 三态退出、Ctrl-D 退出。"""
        kb = KeyBindings()

        # ↑/↓：菜单打开时移动高亮（filter 保证菜单没开时不影响光标移动）。
        @kb.add("up", filter=has_completions)
        def _menu_up(event: KeyPressEvent) -> None:
            event.current_buffer.complete_previous()

        @kb.add("down", filter=has_completions)
        def _menu_down(event: KeyPressEvent) -> None:
            event.current_buffer.complete_next()

        # Esc：菜单打开时关闭菜单（filter 保证菜单没开时 Esc 交给默认处理）。
        @kb.add("escape", filter=has_completions)
        def _close_menu(event: KeyPressEvent) -> None:
            event.current_buffer.cancel_completion()

        @kb.add("enter")
        def _accept(event: KeyPressEvent) -> None:
            buffer = event.current_buffer
            state = buffer.complete_state
            if state is not None and state.completions:
                # 默认取当前高亮项；未显式选中时取第一项。
                comp = state.current_completion or state.completions[0]
                # 若输入行已是该命令的完整形态，回车直接提交；否则先填入命令名。
                if buffer.text == f"/{comp.text}":
                    event.app.exit(result=buffer.text)
                else:
                    buffer.apply_completion(comp)
                return
            # 没有菜单：正常提交，让 app.run() 以这行文本作为返回值结束。
            event.app.exit(result=buffer.text)

        @kb.add("c-c")
        def _interrupt(event: KeyPressEvent) -> None:
            buffer = event.current_buffer
            # 补全菜单开着时，Ctrl-C 先关菜单，不触发退出。
            if buffer.complete_state is not None:
                buffer.cancel_completion()
                return
            if buffer.text:
                # 情况1：有内容 → 清空（这次 Ctrl-C 被"消费"，于是才需要按两次）。
                buffer.reset()
                self._disarm_exit()
            elif self._exit_armed:
                # 情况3：空行 + 窗口内的第二次 → 真正退出。
                self._disarm_exit()
                event.app.exit(exception=QuitSignal())
            else:
                # 情况2：空行 + 首次（或上次已超时复位）→ 待退出 + 起 500ms 定时器。
                self._arm_exit(event.app)

        @kb.add("c-d")
        def _eof(event: KeyPressEvent) -> None:
            buffer = event.current_buffer
            if buffer.text:
                buffer.delete()  # 有内容时 Ctrl-D 删除光标后一个字符（标准行为）
            else:
                event.app.exit(exception=EOFError)  # 空行 Ctrl-D 退出

        return kb


def _border_row(left: str, right: str) -> VSplit:
    """生成一整行水平边框：左角 + 中间用 ─ 填满 + 右角。

    用三个并排的 Window 拼出来（VSplit = 横向排列）：
        - 左右两个宽度固定为 1 的窗口，分别放圆角字符；
        - 中间窗口不设宽度，用 char="─" 把剩余宽度全部填满。
    这样无论终端多宽，边框都能自动补齐。
    """
    return VSplit(
        [
            Window(width=1, height=1, char=left, style="class:frame"),
            Window(height=1, char="─", style="class:frame"),
            Window(width=1, height=1, char=right, style="class:frame"),
        ]
    )
