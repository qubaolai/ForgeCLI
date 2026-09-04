"""基于 prompt_toolkit 的交互式输入。

为什么用完整的 Application 而不是 PromptSession.prompt()：
    prompt() 只能渲染一个"前缀 + 输入"的单行，画不出输入框的上下边框，也放不下
    一个整行宽的命令菜单。必须自己搭 prompt_toolkit
    的布局(Layout)，所以这里直接用底层的 Application。

    代价是：PromptSession 自带的方向键/退格/补全等默认按键，在裸 Application 里
    需要手动挂上——用 load_key_bindings() 一次性加载，再 merge 上我们自己的绑定。


斜杠命令菜单（不同于内置浮动下拉，是自绘的全宽面板）：
    - 输入 "/" 即出现，列出匹配命令，分类、命令名与说明单行对齐；
    - ↑/↓ 移动高亮，Tab 同样可切换；
    - Enter 选中（填入输入行）；命令已完整时 Enter 直接提交；
    - Esc 关闭菜单。

模式切换：
    - 菜单未打开时 Tab 前进、Shift+Tab 后退，沿权限梯度循环切换会话模式（两端截断
      不回绕），切到哪一档由注入的回调决定；新模式经状态栏反馈。菜单打开时 Tab /
      Shift+Tab 仍是菜单前进/后退导航（默认行为）。

退出逻辑：
    - 输入框有内容时按 Ctrl-C → 清空内容（不退出）；
    - 空行第一次 Ctrl-C → 提示行变为"再按一次 Ctrl-C 退出"，并起一个退出窗口定时器；
    - 空行在退出窗口内再次 Ctrl-C → 抛出 SessionExit 退出；
    - 超过 500ms 没有第二次 → 定时器复位"待退出"并重绘，提示行还原（第二次需重新计时）；
    - Ctrl-D 不退出会话：有内容时删除字符，空行时仅取消待退出状态；
    - 一旦开始打字，"待退出"状态立即解除。

只在真终端(TTY)里用本模块；非 TTY 由 bootstrap 直接拒绝 (退出码 NO_TTY)。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.filters import has_completions
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.history import FileHistory, History, InMemoryHistory
from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.defaults import load_key_bindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    HSplit,
    VSplit,
    Window,
    WindowAlign,
)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

from forgecli.interfaces.tui.session_exit import SessionExit

# 命令菜单一屏最多几行候选。超出的按窗口滚动，不是"只显示前 N 条" ——
# 截断的那一版让 /clear 这种排在后面的命令在菜单里根本不存在，而用户看到的是一张
# 看起来完整的表，没有任何东西提示它下面还有。
_MENU_MAX_ROWS = 12
# 滚动时上下各留一行说明还剩多少。
_MENU_MARKER_ROWS = 2
# 输入框、边框与提示行占掉的行数：菜单的可见高度要从终端高度里先减掉它们。
_MENU_CHROME_ROWS = 8

# 输入框最多长到几行. 到顶之后在框内滚动, 光标始终可见.
#
# 有上限是因为输入框长高会把上面的对话往上顶. 粘一篇长文进来时, 用户想看的是自己刚打的
# 那几行, 不是让输入框吃掉整个屏幕.
_INPUT_MAX_ROWS = 8

# Shift+Enter 的 CSI-u 序列 (kitty 键盘协议). prompt_toolkit 3.0.52 没有内置这一条,
# 这里补进去并映射到 ControlJ —— 于是一条 c-j 绑定同时覆盖三种输入方式.
#
# **必须在任何输入被解析之前注册.** vt100 解析器的前缀判定是惰性缓存 (__missing__),
# 一旦为某个前缀缓存过 False 就不会再回头看这张表.
_SHIFT_ENTER_CSI_U = "\x1b[13;2u"

ANSI_SEQUENCES.setdefault(_SHIFT_ENTER_CSI_U, Keys.ControlJ)

# 框下提示行的分组, **按重要性排序** —— 窄终端上从右往左丢.
#
# 换行那一条写 Ctrl+J 而不是 Shift+↵: 后者在一部分终端上根本送不到进程 (见 _newline 的
# 说明), 写上去等于让用户去按一个不生效的键. Ctrl+J 处处可用, 而支持 CSI-u 的终端上
# Shift+↵ 照样能用.
_HINT_GROUPS: tuple[tuple[str, str], ...] = (
    ("/", " 命令"),
    ("↵", " 发送"),
    ("Ctrl+J", " 换行"),
    ("Ctrl+C×2", " 退出"),
)

# 分组之间的间隔.
_HINT_GAP = "   "

# 两次 Ctrl-C 退出的有效间隔（秒）。超过它，第一次按键作废，需重新计时。
_EXIT_WINDOW = 0.8

# 所有界面元素的配色集中在这里，方便统一改主题。
# "class:xxx" 在布局里被引用，这里给出每个 class 对应的样式字符串。
_STYLE = Style.from_dict(
    {
        "frame": "#454b58",  # 输入框边框：灰色
        "prompt": "bold #62d6ad",  # 输入行前缀 "› "
        "hint": "#62d6ad",  # 提示行里的按键，如 "/"、"↵"
        "hint-dim": "#767e8f",  # 提示行里的说明文字
        "hint-alert": "#89a19d",  # "再按一次 Ctrl-C 退出" 警示
        "runtime-status": "#9198a7",  # 输入框下方右侧的模型 / thinking
        "runtime-status-error": "#f4bd61",
        # 斜杠命令菜单（背景透明：不设 bg，终端底色透出来）。
        # 选中项同时使用 ❯、青绿色和深色背景，不依赖单一颜色线索。
        "menu-marker": "bold #62d6ad",
        "menu-category": "#767e8f",
        "menu-name": "#b8bec9",  # 普通行：命令名
        "menu-meta": "#767e8f",  # 普通行：说明（更暗）
        "menu-category-current": "#62d6ad bg:#191d25",
        "menu-name-current": "bold #62d6ad bg:#191d25",
        "menu-meta-current": "#e7e9ee bg:#191d25",
    }
)


@dataclass(frozen=True)
class PromptCommand:
    """输入框需要的命令展示信息；执行仍由 CommandRegistry 负责。"""

    name: str
    summary: str
    category: str = "其他"
    keywords: tuple[str, ...] = ()


class _SlashCompleter(Completer):
    """斜杠命令补全器：当输入以 "/" 开头时，列出匹配的已注册命令。

    prompt_toolkit 的补全机制会在每次输入变化时调用 get_completions，
    我们据此返回候选项；候选项由 ForgePrompt 自绘成全宽菜单。
    """

    def __init__(self, commands: Sequence[tuple[str, str] | PromptCommand]) -> None:
        # 二元组兼容轻量调用方；生产路径传 PromptCommand，额外提供分类与中文关键词。
        self._commands = tuple(
            item if isinstance(item, PromptCommand) else PromptCommand(*item)
            for item in commands
        )
        self.by_name = {item.name: item for item in self._commands}

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
        prefix_matches = [item for item in self._commands if item.name.startswith(word)]
        matches = prefix_matches or [
            item
            for item in self._commands
            if word.casefold()
            in " ".join(
                (item.name, item.summary, item.category, *item.keywords)
            ).casefold()
        ]
        for item in matches:
            if item:
                yield Completion(
                    item.name,
                    # start_position 是负数：从光标往前替换掉已输入的 word 部分，
                    # 这样选中后变成完整的 "/name"，而不是把 name 又接在后面。
                    start_position=-len(word),
                    display=f"/{item.name}",  # 菜单里显示的命令名
                    display_meta=item.summary,  # 菜单里显示的说明
                )


class ForgePrompt:
    """可重复调用的输入提示器：每次 read() 弹出一个带边框的输入框并返回一行输入。"""

    def __init__(
        self,
        commands: Sequence[tuple[str, str] | PromptCommand],
        *,
        status_provider: Callable[[], str] | None = None,
        on_mode_step: Callable[[int], None],
        history_file: Path | None = None,
    ) -> None:
        # "待退出"标志：空行第一次 Ctrl-C 后置 True；它决定提示行是否显示退出警示。
        self._exit_armed = False
        # 待退出复位定时器：第一次 Ctrl-C 起，500ms 内没有第二次就把它复位。
        self._reset_handle: asyncio.TimerHandle | None = None
        # 每次渲染现读：/model 或 /config 改动后下一帧立即反映。
        self._status_provider = status_provider
        # shift+tab 的模式切换回调. 本类是 UI 适配器, 不认识 SessionMode/SessionService,
        # 具体切到哪一档、要不要落事件, 都由注入方 (Repl) 决定.
        self._on_mode_step = on_mode_step

        # 输入缓冲区：挂上斜杠补全器，并开启"边打字边补全"。
        #
        # multiline=True 只影响 Buffer 自己对回车的默认处理；本模块把 enter 显式绑成
        # 提交、把换行绑到另外几个键上（见 _换行键），所以这里开多行不会让回车失去提交
        # 语义。开它是为了让缓冲区能容纳 \n，否则粘贴一段多行文本会被压成一行。
        # 历史落盘, 于是"上次那句长指令"在重启之后还在. 多行缓冲下 ↑/↓ 先在行间走,
        # 到了第一 / 最后一行才翻历史 —— 那是 prompt_toolkit 的默认绑定, 也正是多行
        # 输入框该有的样子.
        self._completer = _SlashCompleter(commands)
        self._buffer = Buffer(
            completer=self._completer,
            complete_while_typing=True,
            multiline=True,
            history=_history(history_file),
        )
        # 用户一开始打字就解除"待退出", 输入会驱散退出提示。
        self._buffer.on_text_changed += self._on_text_changed

        self._app = self._build_app()

    def read(self) -> str:
        """弹出输入框，返回用户输入的一行。

        - 正常回车：返回该行文本；
        - 空行连按两次 Ctrl-C：抛出 SessionExit；
        调用方负责捕获 SessionExit 并据此退出。
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
        # 普通态：列出主要按键，快捷键提示。
        # 窄终端上按重要性从右往左丢. 不丢的话它会和右侧状态撞在一起, 渲染成
        # "Ctrl+C模式 accept_edits" 这种两段文字咬在一起的样子 —— 那比少一个提示更糟.
        return _fit_hint(_HINT_GROUPS, self._hint_budget())

    def _hint_budget(self) -> int:
        """留给左侧提示的列数: 终端宽度减去右侧状态实际占的宽.

        取终端**当前**宽度而不是启动时的: 用户拉窗口是常事, 而这一行每次重绘都会调它.
        """
        columns = get_app().output.get_size().columns
        # StyleAndTextTuples 的元素可能是 2 元也可能是 3 元 (带 mouse handler),
        # 所以按下标取文本而不是解包.
        status = sum(get_cwidth(part[1]) for part in self._bottom_status())
        return columns - status

    def _bottom_status(self) -> StyleAndTextTuples:
        """右侧持续状态：当前模型与该模型的 thinking 配置。"""
        if self._status_provider is None:
            return []
        try:
            status = self._status_provider().strip()
        except Exception:
            return [("class:runtime-status-error", "配置状态不可用  ")]
        if not status:
            return []
        columns = get_app().output.get_size().columns
        return [
            (
                "class:runtime-status",
                f"{_fit_text(status, max(12, columns // 2 - 2))}  ",
            )
        ]

    def _render_menu(self) -> StyleAndTextTuples:
        """自绘斜杠命令菜单：分类、命令与说明单行对齐。

        放不下就按窗口滚动，高亮居中、两端贴边，并标出上下还剩多少条。菜单自己管这件
        事而不是指望终端：它画在 prompt_toolkit 的布局里，终端滚动条碰不到它。

        数据来自缓冲区的补全状态 complete_state：
            - .completions     当前匹配到的候选项列表
            - .complete_index  当前选中的下标（可能为 None，此时默认高亮第一行）
        """
        state = self._buffer.complete_state
        if state is None or not state.completions:
            return []
        completions = state.completions
        selected = state.complete_index if state.complete_index is not None else 0
        visible = self._menu_rows(len(completions))
        start = _menu_window(len(completions), selected, visible)

        name_width = max(get_cwidth(c.display_text) for c in completions) + 2
        category_width = (
            max(
                get_cwidth(self._completer.by_name[c.text].category)
                for c in completions
            )
            + 2
        )

        # 按行组装再用 "\n" 接起来: 让每个分支自己决定要不要补换行, 出过一次
        # "滚动标记后面多一个空行"的问题 —— 标记自带换行, 紧跟的第一行又补了一个.
        lines: list[StyleAndTextTuples] = []
        if start:
            lines.append([("class:menu-meta", f"  ↑ 上面还有 {start} 项")])
        for index in range(start, min(start + visible, len(completions))):
            comp = completions[index]
            current = "-current" if index == selected else ""
            category = self._completer.by_name[comp.text].category
            lines.append(
                [
                    (
                        "class:menu-marker" if index == selected else "",
                        "❯ " if index == selected else "  ",
                    ),
                    (
                        f"class:menu-category{current}",
                        _pad_display(category, category_width),
                    ),
                    (
                        f"class:menu-name{current}",
                        _pad_display(comp.display_text, name_width),
                    ),
                    (f"class:menu-meta{current}", comp.display_meta_text),
                ]
            )
        rest = len(completions) - (start + visible)
        if rest > 0:
            lines.append([("class:menu-meta", f"  ↓ 下面还有 {rest} 项")])

        fragments: StyleAndTextTuples = []
        for position, line in enumerate(lines):
            if position:
                fragments.append(("", "\n"))
            fragments.extend(line)
        return fragments

    def _menu_rows(self, total: int) -> int:
        """这一屏给菜单几行候选。终端拉矮了就少画几行，而不是溢出。"""
        room = get_app().output.get_size().rows - _MENU_CHROME_ROWS
        budget = max(3, min(_MENU_MAX_ROWS, room))
        if total <= budget:
            return total
        return max(3, budget - _MENU_MARKER_ROWS)

    def _build_app(self) -> Application[str]:
        """把缓冲区、布局、按键、样式组装成一个可运行的 Application。"""
        # 输入区窗口：左侧带 "› " 前缀，内容超宽自动折行，框随内容长高。
        #
        # height 用 Dimension(min=1, max=_INPUT_MAX_ROWS) 而不是固定 1:
        # dont_extend_height 让它按内容行数收缩到最小，Dimension 给出增长区间。
        # 少了 max 的话，粘贴一篇长文会把输入框撑满整个终端, 把上面的对话全顶掉;
        # 到顶之后 prompt_toolkit 自己在框内滚动, 光标始终可见。
        input_window = Window(
            BufferControl(buffer=self._buffer),
            get_line_prefix=self._prompt_prefix,
            wrap_lines=True,
            height=self._input_dimension,
            dont_extend_height=True,
        )

        # 斜杠命令菜单面板：放在输入框上方，仅当有补全候选时显示(has_completions)。
        # dont_extend_height 让它按内容行数自适应；Dimension(max=...) 给个上限。
        menu_pane = ConditionalContainer(
            content=Window(
                FormattedTextControl(self._render_menu),
                height=Dimension(min=1, max=_MENU_MAX_ROWS + _MENU_MARKER_ROWS),
                dont_extend_height=True,
            ),
            filter=has_completions,
        )

        # 用 HSplit（纵向堆叠）拼出"菜单 / 上边框 / 输入行 / 下边框 / 提示行"。
        root = HSplit(
            [
                menu_pane,
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
                # 框下同一行：左侧快捷键提示，右侧现读模型 / thinking。
                VSplit(
                    [
                        Window(
                            FormattedTextControl(self._bottom_hint),
                            height=1,
                            dont_extend_width=True,
                        ),
                        Window(
                            FormattedTextControl(self._bottom_status),
                            height=1,
                            align=WindowAlign.RIGHT,
                        ),
                    ]
                ),
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

    def _input_dimension(self) -> Dimension:
        """输入区最多占终端约三分之一；小窗口不再被十行编辑器吞掉。"""
        rows = get_app().output.get_size().rows
        return Dimension(min=1, max=max(3, min(_INPUT_MAX_ROWS, rows // 3)))

    def _key_bindings(self) -> KeyBindings:
        """自定义按键：菜单导航、回车提交、Ctrl-C 三态退出、Ctrl-D 删除。"""
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

        # shift+tab：逆向切换会话模式。prompt_toolkit 默认把 s-tab 绑成
        # menu-complete-backward（菜单反向导航），我们的绑定会覆盖它，所以加
        # ~has_completions：菜单开着时让回默认行为，只有菜单没开时才切模式。
        @kb.add("s-tab", filter=~has_completions)
        def _reverse_switch_mode(event: KeyPressEvent) -> None:
            if self._on_mode_step is None:
                return
            self._on_mode_step(-1)
            # 立刻重绘，让状态栏显示新模式（它就是这个快捷键的视觉反馈）。
            event.app.invalidate()

        # tab：切换会话模式。prompt_toolkit 默认把 tab 绑成
        # menu-complete-backward（菜单反向导航），我们的绑定会覆盖它，所以加
        # ~has_completions：菜单开着时让回默认行为，只有菜单没开时才切模式。
        @kb.add("tab", filter=~has_completions)
        def _forward_switching_mode(event: KeyPressEvent) -> None:
            if self._on_mode_step is None:
                return
            self._on_mode_step(1)
            # 立刻重绘，让状态栏显示新模式（它就是这个快捷键的视觉反馈）。
            event.app.invalidate()

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
            # 没有菜单：正常提交，让 app.run() 以这段文本作为返回值结束。
            #
            # 显式 append_to_history: 平时是 validate_and_handle() 顺手做的, 而这里
            # 直接 exit 绕过了它 —— 不补这一句, ↑ 永远翻不到自己刚说过的话。
            buffer.append_to_history()
            event.app.exit(result=buffer.text)

        # ---- 换行 ----
        #
        # 三个键都插换行, 因为**终端未必送得出 Shift+Enter**:
        #
        # | 终端 | Shift+Enter 实际发什么 |
        # | --- | --- |
        # | kitty / WezTerm / Ghostty | CSI-u `\x1b[13;2u`, 与 Enter 可区分 |
        # | macOS Terminal.app | 就是 `\r`, 与 Enter **完全相同** |
        # | iTerm2 / VS Code / Windows Terminal | 默认同 Enter, 可手动配 |
        #
        # 也就是说在一部分终端上, Shift+Enter 这个需求在按键到达进程之前就已经丢了 ——
        # 代码这一侧做什么都没用. 所以再挂两个一定送得到的:
        #
        # - Ctrl+J: 发 `\n` (ControlJ), 与 Enter 的 `\r` (ControlM) 天然不同码,
        #   处处可用.
        # - Alt/Option+Enter: 发 `\x1b\r`, 绝大多数终端支持
        #   (macOS Terminal 需开 "Option as Meta").
        #
        # Shift+Enter 走 CSI-u 时被映射成 ControlJ (见 _SHIFT_ENTER_CSI_U), 所以一条
        # c-j 绑定就同时覆盖它和 Ctrl+J.
        @kb.add("c-j")
        @kb.add("escape", "enter")
        def _newline(event: KeyPressEvent) -> None:
            buffer = event.current_buffer
            # 补全菜单开着时先关掉: 让它留着, 下一次回车会去选菜单项而不是提交, 而用户
            # 此刻的意图明显是继续写.
            if buffer.complete_state is not None:
                buffer.cancel_completion()
            buffer.insert_text("\n")

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
                event.app.exit(exception=SessionExit())
            else:
                # 情况2：空行 + 首次（或上次已超时复位）→ 待退出 + 起退出窗口定时器。
                self._arm_exit(event.app)

        @kb.add("c-d")
        def _eof(event: KeyPressEvent) -> None:
            buffer = event.current_buffer
            if buffer.text:
                buffer.delete()  # 有内容时 Ctrl-D 删除光标后一个字符（标准行为）
            else:
                self._disarm_exit()  # 退出只接受空行 Ctrl-C×2，空行 Ctrl-D 不退出

        return kb


def _menu_window(total: int, selected: int, visible: int) -> int:
    """可见窗口从第几条开始。高亮尽量居中，到两端就贴边。"""
    if total <= visible:
        return 0
    return min(max(0, selected - visible // 2), total - visible)


def _pad_display(text: str, width: int) -> str:
    """按终端显示列补空格；中文宽字符不能用 str.ljust 对齐。"""
    return text + " " * max(0, width - get_cwidth(text))


def _fit_text(text: str, width: int) -> str:
    """按显示列截断状态文本，给左侧快捷键留出稳定空间。"""
    if get_cwidth(text) <= width:
        return text
    kept: list[str] = []
    used = 0
    for char in text:
        char_width = get_cwidth(char)
        if used + char_width > max(0, width - 1):
            break
        kept.append(char)
        used += char_width
    return "".join(kept) + "…"


def _history(history_file: Path | None) -> History:
    """拿不到磁盘就退回进程内历史: 少一次跨会话的便利, 而不是起不来。"""
    if history_file is None:
        return InMemoryHistory()
    try:
        history_file.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return InMemoryHistory()
    return FileHistory(str(history_file))


def _fit_hint(groups: tuple[tuple[str, str], ...], budget: int) -> StyleAndTextTuples:
    """按可用列数渲染提示行, 放不下的分组整组丢掉.

    整组丢而不是截断文字: 半个 "Ctrl+C×" 比没有更让人困惑.
    """
    fragments: StyleAndTextTuples = [("class:hint-dim", "  ")]
    used = 2
    for index, (key, label) in enumerate(groups):
        gap = _HINT_GAP if index else ""
        width = get_cwidth(gap) + get_cwidth(key) + get_cwidth(label)
        if used + width > budget:
            break
        if gap:
            fragments.append(("class:hint-dim", gap))
        fragments.append(("class:hint", key))
        fragments.append(("class:hint-dim", label))
        used += width
    return fragments


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
