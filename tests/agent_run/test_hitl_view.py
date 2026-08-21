"""HITL 确认界面 (ADR-0016 §8.4, §12.3).

这一节是对 ADR-0013 §14 默认呈现的修订, 所以测试同时钉住"砍了什么"和"绝不能砍什么":

- 砍掉: plan hash, target set hash, 风险标签, 执行画像.
- 保留: 原始命令逐字, 脚本内容完整, **目标集合完整** —— 用户批准的是效果, 不是命令串.

ADR-0028 之后视图不再自己存路径与目标封闭度, 它们由 plan 提供; 因此这里的 `_view`
把入参按"属于 plan 的事实"与"属于视图的事实"分开塞 —— 分不开就说明又有字段抄了两份.
"""

from __future__ import annotations

import dataclasses
import io

import pytest
from rich.console import Console

from forgecli.domain.intents import SessionMode
from forgecli.domain.security.approval import (
    ApprovalBinding,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalView,
)
from forgecli.domain.security.script_facts import ScriptSnapshot
from forgecli.domain.security.vocabulary import ApprovalScope
from forgecli.domain.tool.catalog import ToolCatalog
from forgecli.domain.tool.plan import ContentPreview, TargetResolution
from forgecli.interfaces.cli.tty_approval import (
    TtyApprovalService,
    render_approval_view,
)
from support.fakes import tool_plan

_EMPTY_CATALOG = ToolCatalog(entries=(), reason_tag="test")

# 这些入参描述的是**这次调用是什么**, 归 plan; 其余描述"人看到什么", 归视图.
_PLAN_KEYS = frozenset(
    {
        "raw_command",
        "target_resolution",
        "read_paths",
        "write_paths",
        "delete_paths",
        "move_pairs",
        "network_targets",
        "external_effects",
    }
)


def _view(**overrides: object) -> ApprovalView:
    plan_args: dict[str, object] = {
        "raw_command": "poetry run pytest tests/agent_loop -q",
        "target_resolution": TargetResolution.FORGE_EXPANDED,
        "read_paths": ("/workspace/forge/tests", "/workspace/forge/pyproject.toml"),
    }
    view_args: dict[str, object] = {
        "action_summary": "shell.run: 需要确认",
        "mode": "accept_edits",
        "workspace_roots": ("/workspace/forge", "/workspace/shared"),
        "risk_facts": ("执行了脚本",),
    }
    previews = overrides.pop("content_previews", ())
    for key, value in overrides.items():
        (plan_args if key in _PLAN_KEYS else view_args)[key] = value
    plan = tool_plan(**plan_args)  # type: ignore[arg-type]
    if previews:
        plan = dataclasses.replace(plan, content_previews=previews)  # type: ignore[arg-type]
    return ApprovalView(plan=plan, **view_args)  # type: ignore[arg-type]


def _render(view: ApprovalView) -> str:
    console = Console(file=io.StringIO(), width=100, record=True, no_color=True)
    render_approval_view(console, view)
    return console.export_text()


# ---- 视图组装 ----


def test_counts_include_empty_categories() -> None:
    # "网络 0" 与"根本没提网络"对读者不是一回事.
    assert dict(_view().counts) == {
        "读取": 2,
        "写入": 0,
        "删除": 0,
        "移动": 0,
        "网络": 0,
        "外部副作用": 0,
    }


def test_a_tool_without_a_raw_command_falls_back_to_the_action_summary() -> None:
    assert _view(raw_command=None).raw_command == "shell.run: 需要确认"


def test_unresolved_targets_mark_the_view_as_not_closed() -> None:
    view = _view(
        target_resolution=TargetResolution.DYNAMIC,
        unresolved_reason="写入路径由运行时变量决定",
    )
    assert not view.closed


def test_the_view_reads_paths_from_the_plan_rather_than_a_second_copy() -> None:
    """ADR-0028 规则 B: 视图上不该再有一份可以与 plan 说法不同的路径清单."""
    view = _view(write_paths=("/workspace/forge/a.py",))
    assert dict(view.counts)["写入"] == 1
    assert view.target_set_hash == view.plan.effects.target_set_hash
    fields = {field.name for field in dataclasses.fields(view)}
    assert not fields & {"read_paths", "write_paths", "target_set_hash", "cwd"}


# ---- 渲染 ----


def test_first_line_shows_mode_and_every_workspace_root() -> None:
    output = _render(_view())
    assert "模式 accept_edits" in output
    assert "/workspace/forge" in output
    assert "/workspace/shared" in output, "工作区根不能只显示数量或省略中间项"


def test_the_raw_command_is_shown_verbatim() -> None:
    assert "poetry run pytest tests/agent_loop -q" in _render(_view())


def test_script_content_is_shown_in_full_not_as_a_path() -> None:
    script = "from pathlib import Path\nprint(Path('forge.toml').read_text())"
    output = _render(
        _view(
            raw_command="python3 scripts/x.py",
            script_snapshots=(
                ScriptSnapshot(
                    language="python",
                    origin="file",
                    source=script,
                    path="scripts/x.py",
                ),
            ),
        )
    )
    assert "要执行的 python 代码" in output
    assert "脚本文件" in output
    assert "from pathlib import Path" in output
    assert "print(Path('forge.toml').read_text())" in output


def test_write_content_is_shown_not_just_the_path() -> None:
    """ "写入 README.md"这句话里没有任何能让人做判断的信息, 要看的是内容."""
    output = _render(
        _view(
            raw_command=None,
            action_summary="fs.edit_file: 写入 README.md",
            write_paths=("/workspace/forge/README.md",),
            content_previews=(
                ContentPreview(
                    path="/workspace/forge/README.md",
                    content="# Forge\n\nrm -rf / 之类的内容也要看得见\n",
                ),
            ),
        )
    )
    assert "将变成" in output
    assert "# Forge" in output
    assert "rm -rf / 之类的内容也要看得见" in output


def test_a_dynamic_target_set_is_not_shown_as_closed() -> None:
    """回归: closed 曾经取决于一个没有生产方的字段, 于是 DYNAMIC 一律显示成已封闭."""
    view = _view(
        target_resolution=TargetResolution.DYNAMIC,
        unresolved_reason="npm 的影响范围无法推导",
        write_paths=("/workspace/forge/build",),
    )
    assert view.closed is False
    output = _render(view)
    assert "未封闭原因：npm 的影响范围无法推导" in output
    assert "checkpoint" in output


def test_every_target_is_listed_even_when_there_are_many() -> None:
    """回归: 目标集合不得截断. 省略掉的那一条往往正是用户会拒绝的那条."""
    paths = tuple(f"/workspace/forge/logs/app-{index}.log" for index in range(300))
    console = Console(file=io.StringIO(), width=200, record=True, no_color=True)
    render_approval_view(console, _view(delete_paths=paths))
    output = console.export_text()

    assert "删除 (300 项):" in output
    assert "/workspace/forge/logs/app-0.log" in output
    assert "/workspace/forge/logs/app-299.log" in output
    assert "还有" not in output


def test_unresolved_targets_show_the_reason_and_checkpoint_plan() -> None:
    output = _render(
        _view(
            target_resolution=TargetResolution.DYNAMIC,
            unresolved_reason="写入路径由运行时变量决定",
        )
    )
    assert "未封闭原因：写入路径由运行时变量决定" in output
    assert "执行前会建立 checkpoint" in output


def test_internal_binding_fields_stay_out_of_the_default_view() -> None:
    output = _render(_view())
    for hidden in ("plan_hash", "target_set_hash", "执行了脚本", "sha256:"):
        assert hidden not in output, f"{hidden} 属于内部绑定字段, 不该进确认界面"


def test_control_sequences_in_a_command_cannot_repaint_the_screen() -> None:
    nasty = "rm -rf /tmp/x\x1b[2K\x1b[1A 看起来人畜无害"
    output = _render(_view(raw_command=nasty))
    assert "\x1b" not in output
    assert "rm -rf /tmp/x" in output


def test_rich_markup_in_a_path_is_escaped_not_interpreted() -> None:
    assert "/tmp/[bold]x" in _render(_view(write_paths=("/tmp/[bold]x",)))


# ---- 什么时候列目标 ----


def test_a_read_only_action_does_not_list_targets() -> None:
    """命令已经逐字看过了, 再列一遍它会读哪些文件只是噪音."""
    output = _render(_view())
    assert "目标" not in output
    assert "读取 (" not in output


def test_a_writing_action_always_lists_targets() -> None:
    output = _render(_view(write_paths=("/workspace/forge/a.py",)))
    assert "写入 (1 项):" in output
    assert "/workspace/forge/a.py" in output


def test_an_unclosed_target_set_lists_targets_even_without_writes() -> None:
    output = _render(
        _view(
            target_resolution=TargetResolution.DYNAMIC,
            unresolved_reason="路径由运行时变量决定",
        )
    )
    assert "未封闭原因：路径由运行时变量决定" in output


# ---- 选项 ----


def _request(
    *, mandatory: bool = False, learnable: bool = False, script: bool = False
) -> ApprovalRequest:
    scopes = (
        (ApprovalScope.ONCE, ApprovalScope.WORKSPACE)
        if learnable
        else (ApprovalScope.ONCE,)
    )
    view = _view(
        allowed_scopes=scopes,
        script_snapshots=(
            (ScriptSnapshot(language="python", origin="inline", source="print(1)"),)
            if script
            else ()
        ),
    )
    binding = ApprovalBinding.of(
        view.plan,
        catalog=_EMPTY_CATALOG,
        execution_profile_hash="e",
        policy_version="1",
        mode=SessionMode.ACCEPT_EDITS,
        view_hash=view.view_hash,
    )
    return ApprovalRequest(
        approval_id="apr-1", binding=binding, view=view, mandatory=mandatory
    )


class _ScriptedConsole(Console):
    """把 input 换成脚本化答案, 其余按普通 Console 渲染."""

    def __init__(self, answer: str | type[EOFError]) -> None:
        super().__init__(file=io.StringIO(), width=100, record=True, no_color=True)
        self._answer = answer

    def input(self, *args: object, **kwargs: object) -> str:  # type: ignore[override]
        if self._answer is EOFError:
            raise EOFError
        assert isinstance(self._answer, str)
        return self._answer


@pytest.fixture
def _tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "forgecli.interfaces.cli.tty_approval.stdin_is_tty", lambda: True
    )


def _answer(answer: str | type[EOFError], **kwargs: bool) -> tuple[object, str]:
    console = _ScriptedConsole(answer)
    response = TtyApprovalService(console).request(_request(**kwargs))
    return response, console.export_text()


@pytest.mark.usefixtures("_tty")
def test_options_are_ordered_once_always_deny_with_once_marked_default() -> None:
    _, output = _answer("1", learnable=True)
    assert "[1] once" in output
    assert "[2] always" in output
    assert "[3] deny" in output
    assert (
        output.index("[1] once") < output.index("[2] always") < output.index("[3] deny")
    )
    assert "（默认）" in output


@pytest.mark.usefixtures("_tty")
def test_bare_enter_takes_the_default_once() -> None:
    response, _ = _answer("", learnable=True)
    assert response.outcome is ApprovalOutcome.APPROVED  # type: ignore[union-attr]
    assert response.scope is ApprovalScope.ONCE  # type: ignore[union-attr]


@pytest.mark.usefixtures("_tty")
def test_always_wording_follows_the_subject() -> None:
    """脚本学的是内容, 命令学的是规范化命令; 文案含糊会让人误判范围."""
    _, script_output = _answer("1", learnable=True, script=True)
    assert "相同脚本" in script_output
    _, command_output = _answer("1", learnable=True)
    assert "相同命令" in command_output


@pytest.mark.usefixtures("_tty")
def test_always_says_it_is_workspace_scoped() -> None:
    _, output = _answer("1", learnable=True)
    assert "本工作区内" in output, "不写出范围会让用户以为批的是全局"


@pytest.mark.usefixtures("_tty")
def test_always_maps_to_workspace_scope_not_always_scope() -> None:
    response, _ = _answer("2", learnable=True)
    assert response.outcome is ApprovalOutcome.APPROVED  # type: ignore[union-attr]
    assert response.scope is ApprovalScope.WORKSPACE  # type: ignore[union-attr]


@pytest.mark.usefixtures("_tty")
def test_deny_is_reachable_as_the_third_option() -> None:
    response, _ = _answer("3", learnable=True)
    assert response.outcome is ApprovalOutcome.DENIED  # type: ignore[union-attr]


@pytest.mark.usefixtures("_tty")
def test_mandatory_ask_offers_only_once_and_deny() -> None:
    _, output = _answer("1", mandatory=True)
    assert "always" not in output
    assert "[1] once" in output
    assert "[2] deny" in output


@pytest.mark.usefixtures("_tty")
def test_a_plain_ask_without_learnable_scope_also_hides_always() -> None:
    _, output = _answer("1")
    assert "always" not in output


@pytest.mark.parametrize("answer", ["y", "yes", "9", "0", "-1", "🙂", "no"])
@pytest.mark.usefixtures("_tty")
def test_unrecognised_input_is_denied(answer: str) -> None:
    """认不出来的输入不是"再问一次", 更不是"那就按默认吧"."""
    response, _ = _answer(answer, learnable=True)
    assert response.outcome is ApprovalOutcome.DENIED  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("once", ApprovalScope.ONCE), (" ALWAYS ", ApprovalScope.WORKSPACE)],
)
@pytest.mark.usefixtures("_tty")
def test_the_scope_name_works_as_well_as_its_number(
    answer: str, expected: ApprovalScope
) -> None:
    response, _ = _answer(answer, learnable=True)
    assert response.scope is expected  # type: ignore[union-attr]


@pytest.mark.usefixtures("_tty")
def test_typing_always_when_it_is_not_offered_is_denied() -> None:
    """Mandatory Ask 下没有 always 这个选项, 打出这个词也不能凭空得到学习授权."""
    response, _ = _answer("always", mandatory=True)
    assert response.outcome is ApprovalOutcome.DENIED  # type: ignore[union-attr]


@pytest.mark.usefixtures("_tty")
def test_eof_is_denied() -> None:
    response, _ = _answer(EOFError, learnable=True)
    assert response.outcome is ApprovalOutcome.DENIED  # type: ignore[union-attr]


def test_a_non_tty_stays_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """没人能回答绝不等于放行."""
    monkeypatch.setattr(
        "forgecli.interfaces.cli.tty_approval.stdin_is_tty", lambda: False
    )
    response = TtyApprovalService(_ScriptedConsole("3")).request(_request())
    assert response.outcome is ApprovalOutcome.PENDING
