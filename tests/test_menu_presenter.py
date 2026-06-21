"""菜单 presenter 的按键行为：Ctrl-C 任意层级/状态直接关闭；Esc 逐层返回。

presenter 平时只在真 TTY 跑，这里把 stdin_is_tty / raw_mode / read_key / Live 都换成
假实现，按脚本喂入按键，验证关闭语义。read_key 在脚本耗尽时抛错，从而能断言菜单
是否在预期按键处停下（而不是继续读、或提前返回）。
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest
import rich.live
from rich.console import Console

from forgecli.application.menu import Choice, Menu
from forgecli.interfaces.cli import menu_presenter as mp
from forgecli.interfaces.cli.menu_presenter import RichMenuPresenter
from forgecli.interfaces.cli.tty import Key, KeyPress


class _FakeLive:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def __enter__(self) -> _FakeLive:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def update(self, *args: object, **kwargs: object) -> None:
        pass


@contextlib.contextmanager
def _noop_raw(fd: int) -> Iterator[None]:
    yield


class _FakeStdin:
    def fileno(self) -> int:
        return 0


def _drive(
    menu: Menu, keys: list[KeyPress], monkeypatch: pytest.MonkeyPatch
) -> list[KeyPress]:
    """喂入按键脚本驱动菜单，返回实际被消费的按键序列。"""
    pending = iter(keys)
    consumed: list[KeyPress] = []

    def fake_read_key(fd: int) -> KeyPress:
        try:
            press = next(pending)
        except StopIteration:
            raise AssertionError("菜单未在预期按键处关闭，仍在读取按键") from None
        consumed.append(press)
        return press

    monkeypatch.setattr(mp, "stdin_is_tty", lambda: True)
    monkeypatch.setattr(mp, "raw_mode", _noop_raw)
    monkeypatch.setattr(mp, "read_key", fake_read_key)
    monkeypatch.setattr(mp.sys, "stdin", _FakeStdin())
    monkeypatch.setattr(rich.live, "Live", _FakeLive)

    RichMenuPresenter(Console()).present(menu)
    return consumed


def test_ctrl_c_closes_from_submenu(monkeypatch: pytest.MonkeyPatch) -> None:
    leaf = Menu("leaf", (Choice("x"),))
    root = Menu("root", (Choice("go", submenu=lambda: leaf),))

    # → 进入子菜单(depth 2)，Ctrl-C 应立刻关闭整个菜单，而非只回上一层。
    consumed = _drive(root, [KeyPress(Key.RIGHT), KeyPress(Key.CTRL_C)], monkeypatch)

    assert len(consumed) == 2  # 只读了这两个键就退出（没继续读 → 没只 pop）


def test_ctrl_c_closes_while_editing_without_saving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[str] = []
    root = Menu("root", (Choice("名称", on_text=saved.append),))

    # → 进入行内编辑，输入一个字符，Ctrl-C 应关闭并丢弃（不调用 on_text）。
    consumed = _drive(
        root,
        [KeyPress(Key.RIGHT), KeyPress(Key.CHAR, "a"), KeyPress(Key.CTRL_C)],
        monkeypatch,
    )

    assert len(consumed) == 3
    assert saved == []


def test_ctrl_c_closes_while_searching(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Menu("root", (Choice("a"), Choice("b")))

    consumed = _drive(root, [KeyPress(Key.SLASH), KeyPress(Key.CTRL_C)], monkeypatch)

    assert len(consumed) == 2


def test_esc_returns_one_level_then_closes_at_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leaf = Menu("leaf", (Choice("x"),))
    root = Menu("root", (Choice("go", submenu=lambda: leaf),))

    # → 进子菜单；Esc 回到根（不关闭）；再 Esc 关闭根。三个键都应被消费。
    consumed = _drive(
        root,
        [KeyPress(Key.RIGHT), KeyPress(Key.ESC), KeyPress(Key.ESC)],
        monkeypatch,
    )

    assert len(consumed) == 3
