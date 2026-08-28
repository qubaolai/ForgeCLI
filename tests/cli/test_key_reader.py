"""按键读取的解码约定 (ADR-0040 决策 4.3).

换掉的两份自写适配器一行测试都没有 —— 终端输入难测, 于是就没人测. 但它可测: 开一个
伪终端 (pty), 从主端写字节, 从从端读按键, 和真终端走的是同一条码路.

这些用例不测 prompt-toolkit 的 vt100 解析器本身 (那是它自己的事), 测的是 Forge 这一层的
约定: 阻塞语义, Esc 的判定时机, 以及三个消费者依赖的那几组按键归类.
"""

from __future__ import annotations

import os
import pty
import sys
import threading
import time

import pytest

from forgecli.interfaces.cli.tty.tty import (
    BACKSPACE_KEYS,
    CONFIRM_KEYS,
    KeyReader,
    Keys,
    is_text,
)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="pty 是 POSIX 的; Windows 走 Win32Input"
)


class _Terminal:
    """一个伪终端: `write()` 往主端写, KeyReader 从从端读."""

    def __init__(self) -> None:
        self.master, self.slave = pty.openpty()
        self._stdin = os.fdopen(self.slave, "r", buffering=1)
        self._saved = sys.stdin
        sys.stdin = self._stdin

    def write(self, data: str) -> None:
        os.write(self.master, data.encode("utf-8"))

    def close(self) -> None:
        sys.stdin = self._saved
        self._stdin.close()
        os.close(self.master)


@pytest.fixture
def terminal():
    term = _Terminal()
    try:
        yield term
    finally:
        term.close()


def _keys(term: _Terminal, data: str, count: int) -> list:
    """在 raw 模式**里面**写入, 再读出来.

    顺序是要紧的: 伪终端在进 raw 模式之前还是行缓冲 + ISIG 的, 那时候写进去的 `\x03`
    会被终端驱动当成 SIGINT 发给进程组, 根本不会以字节的形式到达读取端. 真实使用中,
    用户按键时终端已经在 raw 模式里了, 这里照着摆。
    """
    with KeyReader() as reader:
        term.write(data)
        return [reader.read() for _ in range(count)]


def test_printable_characters_come_back_as_text(terminal) -> None:
    presses = _keys(terminal, "ab", 2)
    assert all(is_text(p) for p in presses)
    assert "".join(p.data for p in presses) == "ab"


def test_multibyte_characters_are_not_split(terminal) -> None:
    """换掉的实现自己算 UTF-8 首字节的续读长度; 这条盯的就是那段逻辑的替代品."""
    presses = _keys(terminal, "中文", 2)
    assert all(is_text(p) for p in presses)
    assert "".join(p.data for p in presses) == "中文"


def test_arrow_keys_are_decoded(terminal) -> None:
    assert [p.key for p in _keys(terminal, "\x1b[A\x1b[B\x1b[C\x1b[D", 4)] == [
        Keys.Up,
        Keys.Down,
        Keys.Right,
        Keys.Left,
    ]


def test_application_cursor_arrows_are_decoded(terminal) -> None:
    """`ESC O A` 是 application cursor 模式下的 ↑. 换掉的实现认不出它, 会当成 Esc."""
    assert _keys(terminal, "\x1bOA", 1)[0].key is Keys.Up


def test_lone_escape_is_reported_after_the_grace_period(terminal) -> None:
    """单独的 Esc 必须自己送出来, 不能一直等下一个字节 —— 菜单靠它返回上一层."""
    started = time.monotonic()
    press = _keys(terminal, "\x1b", 1)[0]
    elapsed = time.monotonic() - started
    assert press.key is Keys.Escape
    assert elapsed < 1.0  # 等一小会儿可以, 等到下一次按键不行


def test_escape_then_arrow_is_one_key_not_two(terminal) -> None:
    """一次按下 ↑ 产生的三个字节, 不能被拆成 Esc + [ + A."""
    assert _keys(terminal, "\x1b[A", 1)[0].key is Keys.Up


def test_control_c_is_a_key_not_a_signal(terminal) -> None:
    """raw mode 关掉 ISIG, 所以 Ctrl-C 是一个普通按键, 由调用方决定怎么处理."""
    assert _keys(terminal, "\x03", 1)[0].key is Keys.ControlC


def test_enter_accepts_both_carriage_return_and_newline(terminal) -> None:
    assert all(p.key in CONFIRM_KEYS for p in _keys(terminal, "\r\n", 2))


def test_backspace_accepts_both_encodings(terminal) -> None:
    """终端发 0x7f 还是 0x08 取决于配置; 行内编辑不该因此少一个退格."""
    assert all(p.key in BACKSPACE_KEYS for p in _keys(terminal, "\x7f\x08", 2))


def test_read_blocks_until_a_key_arrives(terminal) -> None:
    """阻塞是这一层的约定: 三个消费者都是"画一帧, 等一个键"的循环."""
    got: list[object] = []

    def later() -> None:
        time.sleep(0.2)
        terminal.write("x")

    with KeyReader() as reader:
        worker = threading.Thread(target=later)
        worker.start()
        started = time.monotonic()
        got.append(reader.read())
        elapsed = time.monotonic() - started
        worker.join()

    assert elapsed >= 0.15  # 真的等了, 不是空转拿到的
    assert got[0].data == "x"  # type: ignore[attr-defined]


def test_slash_is_an_ordinary_character(terminal) -> None:
    """换掉的实现给 "/" 单开了一个 Key.SLASH.

    那是它自己的解析器留下的痕迹, 不是终端事实: 终端送来的就是一个普通字符.
    """
    press = _keys(terminal, "/", 1)[0]
    assert is_text(press)
    assert press.data == "/"
