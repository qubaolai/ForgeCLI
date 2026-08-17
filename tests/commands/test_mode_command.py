"""/mode: 查看当前模式 + 选择面板 + 直达切换 (02-detailed-design §5.3)."""

from __future__ import annotations

import io

from rich.console import Console

from forgecli.application.menu import Menu
from forgecli.domain.intents import SessionMode, SlashCommand
from forgecli.interfaces.cli.commands.mode_command import ModeSelectCommand
from forgecli.interfaces.cli.output import RichOutput


class _Snapshot:
    def __init__(self, mode: SessionMode) -> None:
        self.mode = mode


class _Session:
    def __init__(self, mode: SessionMode = SessionMode.ACCEPT_EDITS) -> None:
        self._mode = mode
        self.switches: list[SessionMode] = []

    def current(self) -> _Snapshot:
        return _Snapshot(self._mode)

    def set_mode(self, mode: SessionMode) -> None:
        self._mode = mode
        self.switches.append(mode)


class _Presenter:
    """记录被展示的菜单; 需要时替用户点某一项."""

    def __init__(self, pick: str | None = None) -> None:
        self.menus: list[Menu] = []
        self._pick = pick

    def present(self, menu: Menu) -> None:
        self.menus.append(menu)
        if self._pick is None:
            return
        for choice in menu.choices:
            if choice.label == self._pick and choice.on_select is not None:
                choice.on_select()


def _run(
    args: tuple[str, ...],
    session: _Session,
    presenter: _Presenter,
) -> str:
    console = Console(file=io.StringIO(), width=100, no_color=True)
    command = ModeSelectCommand(
        session,  # type: ignore[arg-type]
        presenter,  # type: ignore[arg-type]
        RichOutput(console),
    )
    command.execute(SlashCommand("/mode", "mode", args))
    stream = console.file
    assert isinstance(stream, io.StringIO)
    return stream.getvalue()


def test_bare_mode_shows_the_current_one_then_opens_the_panel() -> None:
    session = _Session(SessionMode.PLAN)
    presenter = _Presenter()

    text = _run((), session, presenter)

    assert "plan" in text
    assert len(presenter.menus) == 1
    assert [choice.label for choice in presenter.menus[0].choices] == [
        "plan",
        "accept_edits",
        "auto",
        "full_access",
    ]  # 权限从紧到松, 顺序本身是安全信息


def test_picking_from_the_panel_switches_mode() -> None:
    session = _Session(SessionMode.PLAN)

    _run((), session, _Presenter(pick="auto"))

    assert session.switches == [SessionMode.AUTO]


def test_the_current_mode_is_marked_in_the_panel() -> None:
    session = _Session(SessionMode.AUTO)
    presenter = _Presenter()

    _run((), session, presenter)

    previews = {
        choice.label: choice.preview() if choice.preview else ""
        for choice in presenter.menus[0].choices
    }
    assert previews["auto"].startswith("●")
    assert not previews["plan"].startswith("●")


def test_an_argument_switches_directly_without_the_panel() -> None:
    session = _Session(SessionMode.PLAN)
    presenter = _Presenter()

    _run(("auto",), session, presenter)

    assert session.switches == [SessionMode.AUTO]
    assert presenter.menus == []


def test_hyphens_and_underscores_both_work() -> None:
    """命令名用连字符 (/accept-edits), 落盘取值用下划线; 用户不必记住这个区别."""
    session = _Session(SessionMode.PLAN)

    _run(("accept-edits",), session, _Presenter())

    assert session.switches == [SessionMode.ACCEPT_EDITS]


def test_an_unknown_mode_changes_nothing() -> None:
    session = _Session(SessionMode.PLAN)

    text = _run(("yolo",), session, _Presenter())

    assert session.switches == []
    assert "未知模式" in text


def test_switching_to_the_current_mode_writes_no_event() -> None:
    """没有变化就不该产生 mode_changed —— 事件日志里的每条都应对应一次真实变化."""
    session = _Session(SessionMode.AUTO)

    _run(("auto",), session, _Presenter())

    assert session.switches == []
