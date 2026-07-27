"""/resume 命令：菜单列表 / 搜索 / 恢复（续写）/ 跨项目守卫。

无参与搜索走 MenuPresenter（与 /config 一致）；这里用 fake presenter 捕获菜单并可模拟
Enter 选中，避免依赖真 TTY。直接 /resume <session_id> 不经菜单，立即恢复。
"""

from __future__ import annotations

from pathlib import Path

from forgecli.application.agent_loop import (
    AgentLoop,
    AnswerAction,
    LoopDecision,
    LoopInput,
    LoopObservation,
    LoopStepResult,
    LoopStop,
    LoopStopReason,
)
from forgecli.application.agent_turn import AgentTurnService
from forgecli.application.interaction_ports import MenuPresenter, UserOutput
from forgecli.application.menu import Menu
from forgecli.application.project import ProjectConfig, ProjectContext
from forgecli.application.session import (
    EventType,
    ResumeService,
    SessionEvent,
    SessionService,
    SessionSnapshot,
)
from forgecli.domain.intents import SessionMode, SlashCommand
from forgecli.infrastructure.session import (
    FsSessionCatalog,
    JsonlEventStore,
    JsonStateStore,
)
from forgecli.interfaces.cli.commands.resume_command import ResumeCommand


class _EchoLoop(AgentLoop):
    """脚本化单步 loop：固定回答，供本文件驱动 AgentTurnService。"""

    def start(self, loop_input: LoopInput) -> LoopStepResult:
        return LoopDecision(next_action=AnswerAction(text="好的"))

    def observe(self, observation: LoopObservation) -> LoopStepResult:
        return LoopStop.of(LoopStopReason.FINAL_ANSWER)


def _agent_turn(session: SessionService) -> AgentTurnService:
    return AgentTurnService(session, loop_factory=_EchoLoop)


class _RecordingOutput(UserOutput):
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, message: str) -> None:
        self.lines.append(message)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class _FakePresenter(MenuPresenter):
    """捕获被展示的菜单；可选地模拟在某一行按 Enter（触发其 on_select）。"""

    def __init__(self, *, select: int | None = None) -> None:
        self.menus: list[Menu] = []
        self._select = select

    def present(self, menu: Menu) -> None:
        self.menus.append(menu)
        if self._select is not None:
            choice = list(menu.choices)[self._select]
            assert choice.on_select is not None
            choice.on_select()

    @property
    def menu(self) -> Menu:
        assert len(self.menus) == 1
        return self.menus[0]


def _context(root: str = "/work") -> ProjectContext:
    return ProjectContext(
        ProjectConfig(
            project_id="repo-deadbeef",
            trusted=True,
            primary_workspace_root=root,
            workspace_roots=(root,),
        )
    )


def _make_history(
    sessions: Path,
    session_id: str,
    *,
    title: str,
    workspace_root: str = "/work",
    user_count: int = 2,
) -> SessionSnapshot:
    events = JsonlEventStore(sessions)
    seq = 0

    def emit(event_type: EventType, payload: dict[str, object]) -> str:
        nonlocal seq
        seq += 1
        eid = f"evt_{seq:04d}"
        events.append(
            SessionEvent(
                event_id=eid,
                session_id=session_id,
                type=event_type,
                created_at="2026-06-29T10:00:00+08:00",
                payload=payload,
            )
        )
        return eid

    last = emit(EventType.SESSION_CREATED, {"workspace_root": workspace_root})
    for i in range(user_count):
        turn = f"turn_{i+1:04d}"
        last = emit(EventType.USER_MESSAGE, {"turn_id": turn, "text": "hi"})
        last = emit(EventType.ASSISTANT_MESSAGE, {"turn_id": turn, "text": "ok"})

    snapshot = SessionSnapshot(
        session_id=session_id,
        workspace_root=workspace_root,
        mode=SessionMode.PLAN,
        last_event_id=last,
        updated_at="2026-06-29T10:00:00+08:00",
        title=title,
    )
    JsonStateStore(sessions).write(snapshot)
    return snapshot


def _live_session(sessions: Path, root: str = "/work") -> SessionService:
    service = SessionService(
        JsonlEventStore(sessions),
        JsonStateStore(sessions),
        workspace_root=root,
        id_factory=lambda: "live-sid",
    )
    service.start()
    return service


def _command(
    sessions: Path,
    session: SessionService,
    agent_turn: AgentTurnService,
    *,
    presenter: MenuPresenter | None = None,
    root: str = "/work",
) -> tuple[ResumeCommand, _RecordingOutput]:
    output = _RecordingOutput()
    service = ResumeService(
        FsSessionCatalog(sessions),
        JsonStateStore(sessions),
        JsonlEventStore(sessions),
    )
    cmd = ResumeCommand(
        service,
        session,
        agent_turn,
        _context(root),
        presenter or _FakePresenter(),
        output,
    )
    return cmd, output


def test_no_args_no_history_prints_message(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    session = _live_session(sessions)
    presenter = _FakePresenter()
    cmd, output = _command(sessions, session, _agent_turn(session), presenter=presenter)

    cmd.execute(SlashCommand(raw_text="/resume", command="resume"))

    assert output.text == "暂无可恢复会话。"
    assert presenter.menus == []  # 无会话不弹菜单


def test_no_args_presents_menu_with_title_and_summary(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    _make_history(sessions, "hist-1", title="重构登录")
    session = _live_session(sessions)
    presenter = _FakePresenter()
    cmd, _ = _command(sessions, session, _agent_turn(session), presenter=presenter)

    cmd.execute(SlashCommand(raw_text="/resume", command="resume"))

    menu = presenter.menu
    assert menu.title == "恢复历史会话"
    choice = list(menu.choices)[0]
    assert choice.label == "重构登录"
    assert choice.preview is not None and "plan" in choice.preview()
    # 空格预览的摘要含会话 id
    assert choice.payload is not None and "hist-1" in choice.payload()


def test_menu_selection_resumes(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    _make_history(sessions, "hist-1", title="续写测试", user_count=2)
    session = _live_session(sessions)
    agent_turn = _agent_turn(session)
    presenter = _FakePresenter(select=0)  # 模拟在第 1 行 Enter
    cmd, output = _command(sessions, session, agent_turn, presenter=presenter)

    cmd.execute(SlashCommand(raw_text="/resume", command="resume"))

    assert "已恢复会话" in output.text
    assert session.current().session_id == "hist-1"
    assert session.current().mode == SessionMode.PLAN
    # _seq 续接：历史 evt_0005 -> 下一条 evt_0006
    assert session.record_slash_command("noop").event_id == "evt_0006"
    # turn 计数续接：历史 2 个 user_message -> 下一轮 turn_0003
    assert agent_turn.handle_user_message("继续").turn_id == "turn_0003"


def test_direct_session_id_resumes_without_menu(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    _make_history(sessions, "hist-1", title="直达", user_count=1)
    session = _live_session(sessions)
    presenter = _FakePresenter()
    cmd, output = _command(sessions, session, _agent_turn(session), presenter=presenter)

    cmd.execute(
        SlashCommand(raw_text="/resume hist-1", command="resume", args=("hist-1",))
    )

    assert "已恢复会话" in output.text
    assert session.current().session_id == "hist-1"
    assert presenter.menus == []  # 显式 id 不经菜单


def test_resume_cross_project_blocked(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    _make_history(sessions, "other", title="别的项目", workspace_root="/elsewhere")
    session = _live_session(sessions, root="/work")
    cmd, output = _command(sessions, session, _agent_turn(session), root="/work")

    cmd.execute(
        SlashCommand(raw_text="/resume other", command="resume", args=("other",))
    )

    assert "跨目录(项目)恢复暂未实现" in output.text
    assert session.current().session_id == "live-sid"  # 未 hydrate


def test_non_id_arg_is_search(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    _make_history(sessions, "hist-1", title="重构登录")
    session = _live_session(sessions)

    presenter = _FakePresenter()
    cmd, _ = _command(sessions, session, _agent_turn(session), presenter=presenter)
    cmd.execute(SlashCommand(raw_text="/resume 登录", command="resume", args=("登录",)))
    assert "匹配「登录」" in presenter.menu.title
    assert list(presenter.menu.choices)[0].label == "重构登录"

    miss_presenter = _FakePresenter()
    miss_cmd, miss_output = _command(
        sessions, session, _agent_turn(session), presenter=miss_presenter
    )
    miss_cmd.execute(
        SlashCommand(raw_text="/resume zzz", command="resume", args=("zzz",))
    )
    assert miss_output.text == "未找到匹配「zzz」的会话。"
    assert miss_presenter.menus == []
