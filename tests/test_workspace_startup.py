"""WorkspaceStartup 四分支：命中绑定 / 非 TTY / 询问后信任 / 拒绝。

交互用 fake TrustPrompter 注入，不依赖真实终端。
"""

from __future__ import annotations

from pathlib import Path

from forgecli.application.interaction_ports import TrustPrompter
from forgecli.application.project import (
    ProjectService,
    WorkspaceStartup,
)
from forgecli.infrastructure.project import (
    TomlProjectConfigStore,
    TomlProjectIndexStore,
)


class _FakePrompter(TrustPrompter):
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.asked: list[str] = []

    def confirm(self, path: str) -> bool:
        self.asked.append(path)
        return self.answer


def _service(home: Path) -> ProjectService:
    projects = home / "projects"
    return ProjectService(
        TomlProjectIndexStore(projects / "index.toml"),
        TomlProjectConfigStore(projects),
        clock=lambda: "2026-06-26T10:00:00+08:00",
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


def test_already_trusted_binds_without_prompt(tmp_path: Path) -> None:
    service = _service(tmp_path / "home")
    repo = _repo(tmp_path)
    service.trust(repo)
    prompter = _FakePrompter(answer=False)

    result = WorkspaceStartup(service, prompter).resolve(repo, interactive=True)

    assert result.reason == "bound"
    assert result.project is not None
    assert prompter.asked == []  # 命中不再询问


def test_non_tty_without_trust_does_not_prompt_or_create(tmp_path: Path) -> None:
    home = tmp_path / "home"
    service = _service(home)
    repo = _repo(tmp_path)
    prompter = _FakePrompter(answer=True)

    result = WorkspaceStartup(service, prompter).resolve(repo, interactive=False)

    assert result.reason == "no_tty"
    assert result.project is None
    assert prompter.asked == []
    assert not (home / "projects").exists()


def test_accept_trust_creates_project(tmp_path: Path) -> None:
    service = _service(tmp_path / "home")
    repo = _repo(tmp_path)
    prompter = _FakePrompter(answer=True)

    result = WorkspaceStartup(service, prompter).resolve(repo, interactive=True)

    assert result.reason == "trusted"
    assert result.project is not None
    assert prompter.asked == [str(repo.resolve())]  # 展示绝对路径
    assert service.find_trusted(repo) is not None


def test_decline_trust_creates_nothing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    service = _service(home)
    repo = _repo(tmp_path)
    prompter = _FakePrompter(answer=False)

    result = WorkspaceStartup(service, prompter).resolve(repo, interactive=True)

    assert result.reason == "declined"
    assert result.project is None
    assert not (home / "projects").exists()
