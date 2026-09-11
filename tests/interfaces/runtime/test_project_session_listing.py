from __future__ import annotations

from typing import cast

import pytest

from forgecli.application.project.project_service import ProjectService
from forgecli.domain.session.snapshot import SessionSnapshot
from forgecli.domain.workspace.project import ProjectConfig
from forgecli.infrastructure.session.json_state_store import JsonStateStore
from forgecli.interfaces.runtime import project_runtime
from forgecli.interfaces.runtime.project_runtime import ProjectRuntimeRegistry


class _Projects:
    def __init__(self, project: ProjectConfig) -> None:
        self._project = project

    def get(self, project_id: str) -> ProjectConfig | None:
        return self._project if project_id == self._project.project_id else None


def _snapshot(session_id: str, updated_at: str) -> SessionSnapshot:
    return SessionSnapshot(
        session_id=session_id,
        workspace_root="/workspace",
        last_event_id=None,
        updated_at=updated_at,
        title=session_id,
    )


def test_lists_a_project_page_without_activating_it(tmp_path, monkeypatch) -> None:
    project = ProjectConfig("project-1", True, "/workspace")
    sessions_dir = tmp_path / "projects" / project.project_id / "sessions"
    states = JsonStateStore(sessions_dir)
    states.write(_snapshot("oldest", "2026-09-09T12:00:00"))
    states.write(_snapshot("middle", "2026-09-10T12:00:00"))
    states.write(_snapshot("newest", "2026-09-11T12:00:00"))
    monkeypatch.setattr(project_runtime, "config_dir", lambda: tmp_path)

    registry = ProjectRuntimeRegistry(cast(ProjectService, _Projects(project)))

    page = registry.list_sessions(project.project_id, offset=1, limit=2)

    assert [item.session_id for item in page] == ["middle", "oldest"]
    assert registry.active is None


def test_rejects_an_unknown_project(tmp_path, monkeypatch) -> None:
    project = ProjectConfig("project-1", True, "/workspace")
    monkeypatch.setattr(project_runtime, "config_dir", lambda: tmp_path)
    registry = ProjectRuntimeRegistry(cast(ProjectService, _Projects(project)))

    with pytest.raises(KeyError):
        registry.list_sessions("missing")
