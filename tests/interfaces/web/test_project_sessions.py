from __future__ import annotations

import asyncio
from typing import cast

from fastapi import Request

from forgecli.domain.session.snapshot import SessionSnapshot
from forgecli.interfaces.runtime.project_runtime import ProjectRuntimeRegistry
from forgecli.interfaces.web.routers import projects


def _snapshot(session_id: str) -> SessionSnapshot:
    return SessionSnapshot(
        session_id=session_id,
        workspace_root="/workspace",
        last_event_id=None,
        updated_at="2026-09-11T12:00:00",
        title=session_id,
    )


class _Registry:
    def __init__(self) -> None:
        self.call: tuple[str, int, int] | None = None

    def list_sessions(
        self, project_id: str, *, offset: int, limit: int
    ) -> list[SessionSnapshot]:
        self.call = (project_id, offset, limit)
        return [_snapshot("session-2"), _snapshot("session-3"), _snapshot("lookahead")]


def test_project_session_page_exposes_next_offset(monkeypatch) -> None:
    registry = _Registry()
    monkeypatch.setattr(projects, "registry", lambda _request: registry)

    result = asyncio.run(
        projects.list_project_sessions(
            "project-1",
            cast(Request, object()),
            offset=5,
            limit=2,
        )
    )

    assert registry.call == ("project-1", 5, 3)
    assert [item["session_id"] for item in result["items"]] == [
        "session-2",
        "session-3",
    ]
    assert result == {
        "project_id": "project-1",
        "items": result["items"],
        "offset": 5,
        "limit": 2,
        "has_more": True,
        "next_offset": 7,
    }


def test_last_project_session_page_has_no_next_offset(monkeypatch) -> None:
    registry = _Registry()
    monkeypatch.setattr(
        registry,
        "list_sessions",
        lambda _project_id, *, offset, limit: [_snapshot("last")],
    )
    monkeypatch.setattr(projects, "registry", lambda _request: registry)

    result = asyncio.run(
        projects.list_project_sessions(
            "project-1",
            cast(Request, object()),
            offset=10,
            limit=5,
        )
    )

    assert result["has_more"] is False
    assert result["next_offset"] is None
