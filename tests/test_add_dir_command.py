"""/add-dir：fake picker 驱动添加目录、非法路径友好报错、取消无副作用。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from forgecli.application.interaction_ports import DirectoryPicker, UserOutput
from forgecli.application.project import ProjectContext, ProjectService
from forgecli.domain.intents import SlashCommand
from forgecli.infrastructure.project import (
    TomlProjectConfigStore,
    TomlProjectIndexStore,
)
from forgecli.interfaces.cli.commands.add_dir_command import AddDirCommand


class _RecordingOutput(UserOutput):
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, message: str) -> None:
        self.lines.append(message)


class _FakePicker(DirectoryPicker):
    def __init__(self, answer: str | None) -> None:
        self.answer = answer
        self.initial_subdirs: list[str] = []
        self.list_subdirs: Callable[[str], Sequence[str]] | None = None

    def pick(self, list_subdirs: Callable[[str], Sequence[str]]) -> str | None:
        self.list_subdirs = list_subdirs
        self.initial_subdirs = list(list_subdirs(""))  # 初始列出当前目录
        return self.answer


def _setup(
    tmp_path: Path, picker: DirectoryPicker
) -> tuple[AddDirCommand, ProjectContext, _RecordingOutput]:
    projects = tmp_path / "home" / "projects"
    service = ProjectService(
        TomlProjectIndexStore(projects / "index.toml"),
        TomlProjectConfigStore(projects),
        clock=lambda: "2026-06-26T10:00:00+08:00",
    )
    context = ProjectContext(service.trust(tmp_path))  # 信任 cwd 自身
    output = _RecordingOutput()
    return AddDirCommand(context, service, picker, output), context, output


def _add_dir() -> SlashCommand:
    return SlashCommand(raw_text="/add-dir", command="add-dir")


def test_add_existing_dir_updates_workspace_and_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # base = cwd
    target = tmp_path / "pkg"
    target.mkdir()
    picker = _FakePicker(answer="pkg")
    command, context, output = _setup(tmp_path, picker)

    command.execute(_add_dir())

    assert str(target.resolve()) in context.project.workspace_roots
    assert any("已添加" in line for line in output.lines)
    assert "pkg" in picker.initial_subdirs


def test_picker_can_list_typed_subdir_subdirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Tab 触发的 list_subdirs(raw)：列出输入路径下的子目录（支持逐层下钻）。
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pkg" / "sub").mkdir(parents=True)
    (tmp_path / "pkg" / "sub2").mkdir()
    picker = _FakePicker(answer=None)
    command, _, _ = _setup(tmp_path, picker)

    command.execute(_add_dir())

    assert picker.list_subdirs is not None
    assert "pkg" in picker.initial_subdirs  # 空输入 -> 当前目录
    assert list(picker.list_subdirs("pkg")) == ["sub", "sub2"]  # 下钻到 pkg
    assert list(picker.list_subdirs("pkg/sub")) == []  # 叶子目录无子目录
    assert list(picker.list_subdirs("nope")) == []  # 不存在 -> 空，不抛错


def test_invalid_path_shows_friendly_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    command, context, output = _setup(tmp_path, _FakePicker(answer="nope"))
    before = context.project.workspace_roots

    command.execute(_add_dir())

    assert context.project.workspace_roots == before
    assert output.lines and "不存在" in output.lines[0]


def test_cancel_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    command, context, output = _setup(tmp_path, _FakePicker(answer=None))
    before = context.project.workspace_roots

    command.execute(_add_dir())

    assert context.project.workspace_roots == before
    assert output.lines == []
