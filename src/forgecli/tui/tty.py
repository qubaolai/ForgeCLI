"""按键读取: raw mode 与 vt100 解析交给 prompt-toolkit (ADR-0040 决策 4.3).

自己写一份终端适配器是走过一遍的弯路: 手动改 termios 标志位, 自己判 UTF-8 首字节的
续读长度, 自己解 `ESC [ A` 这类 CSI 序列. 那些判断也不完整 —— 只认 `ESC [ A..D` 四个
方向, 认不出 `ESC O A` (application cursor 模式, 一部分终端默认就是它), 认不出
Home/End/Delete/PageUp, 也认不出带修饰键的 `ESC [ 1 ; 5 A`. 缺哪一条都不会报错, 只会让
某个终端上的某个键"按了没反应".

## 为什么还留一个自己的循环

`Application.run()` 要接管整块屏幕, 而菜单与审批卡片都是 `rich.Live` 就地重绘的.
所以这里只取 prompt-toolkit 的**输入**部分: `create_input()` 给跨平台的 raw mode 与
按键解析, 循环和渲染仍是我们自己的.

一个必须自己处理的细节是 Esc: `ESC` 本身就是方向键序列的头一个字节, 所以收到它之后
必须等一小会儿才能断定用户按的是 Esc 而不是 ↑. `Application` 平时用事件循环的定时器
做这件事; 这里没有事件循环, 所以用一次带超时的 `select` 代替, 超时后调 `flush_keys()`.

raw mode 用 prompt-toolkit 的实现是安全的: 它只改 LFLAG 与 IFLAG, **不动 OFLAG**, 于是
OPOST/ONLCR 保留, `"\\n"` 仍输出成 `"\\r\\n"`. 这一条是 `rich.Live` 就地重绘的前提 ——
关掉它, 换行后光标不回行首, 每帧会层层右移堆在屏幕上.
"""

from __future__ import annotations

import contextlib
import select
import sys
from types import TracebackType

from prompt_toolkit.input import create_input
from prompt_toolkit.key_binding.key_processor import KeyPress
from prompt_toolkit.keys import Keys

__all__ = [
    "BACKSPACE_KEYS",
    "CONFIRM_KEYS",
    "KeyPress",
    "KeyReader",
    "Keys",
    "is_text",
    "stdin_is_tty",
]

# Enter 在原始终端上是 `\r` (ControlM); `\n` (ControlJ) 只在粘贴时出现. 两个都收.
CONFIRM_KEYS = frozenset({Keys.ControlM, Keys.ControlJ})

# 退格键在不同终端上发 0x7f 或 0x08, prompt-toolkit 把两者都归成 ControlH.
BACKSPACE_KEYS = frozenset({Keys.ControlH})

# 收到 ESC 之后再等多久, 才认定它是"单独的 Esc"而不是某个转义序列的开头.
#
# 50ms: 方向键的后续字节是同一次按键产生的, 它们之间不会隔这么久; 而人手按 Esc 之后
# 50ms 内不可能再按下一个键. 取更小的值 (比如 0.5ms) 会在慢终端 (ssh, tmux 嵌套) 上把
# 方向键拆成 "Esc + [ + A" 三次按键, 表现为按一下 ↑ 直接退出了菜单.
_ESCAPE_GRACE_SECONDS = 0.05


def stdin_is_tty() -> bool:
    """交互路径要求标准输入和标准输出都连接终端."""
    return sys.stdin.isatty() and sys.stdout.isatty()


class KeyReader:
    """阻塞式逐键读取; 作为上下文管理器进入 / 退出 raw mode.

    不是线程安全的, 也不该是: 同一个终端同一时刻只有一个东西在读它.
    """

    def __init__(self) -> None:
        # 不传 always_prefer_tty: 那个开关会在 stdin 是管道时改去读 stdout/stderr 的
        # 终端. 调用方已经用 stdin_is_tty() 拦过一道, 所以这里再去猜一个别的 fd, 只会
        # 让"输入到底从哪来"变得说不清.
        self._input = create_input()
        self._pending: list[KeyPress] = []
        self._raw: contextlib.AbstractContextManager[None] | None = None

    def __enter__(self) -> KeyReader:
        raw = self._input.raw_mode()
        raw.__enter__()
        self._raw = raw
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._raw is not None:
            self._raw.__exit__(exc_type, exc, tb)
            self._raw = None

    def read(self) -> KeyPress:
        """阻塞直到读到一个按键.

        stdin 走到尽头时返回 Ctrl-C: 消费者都把它当"关掉整个界面", 而输入没了正是该
        关掉界面的时候. 返回一个"什么也不是"的键会让调用方的循环空转.
        """
        while not self._pending:
            if self._input.closed:
                return KeyPress(Keys.ControlC, "\x03")
            keys = self._input.read_keys()
            if not keys and not self._more_input_pending():
                keys = self._input.flush_keys()
            self._pending.extend(keys)
        return self._pending.pop(0)

    def _more_input_pending(self) -> bool:
        """解析器手里压着半条序列时, 后续字节会不会马上到.

        拿不到 fileno 的输入实现 (Windows 控制台) 直接当"不会到": 它的 read_keys()
        返回的就是完整按键, 走不到这条分支, 而 flush_keys() 在那边是空操作.
        """
        try:
            fileno = self._input.fileno()
        except (AttributeError, NotImplementedError, OSError, ValueError):
            return False
        ready, _, _ = select.select([fileno], [], [], _ESCAPE_GRACE_SECONDS)
        return bool(ready)


def is_text(press: KeyPress) -> bool:
    """这一下按的是可打印字符, 而不是功能键.

    prompt-toolkit 用 `key` 的类型区分两者: 功能键是 `Keys` 成员, 普通字符的 `key` 就是
    那个字符本身 (一个 str). `Keys` 自己也是 str 枚举, 所以判据只能是 isinstance,
    不能是"看起来像不像字符".

    还要过一道 isprintable: 没被 `Keys` 收录的控制字符会落到这条分支上, 而把它们插进
    输入只会显示成乱码.
    """
    return not isinstance(press.key, Keys) and press.data.isprintable()
