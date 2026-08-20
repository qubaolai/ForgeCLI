from dataclasses import dataclass

import pytest

from forgecli.interfaces.web.runtime import ProjectRuntime
from forgecli.shared.errors import SessionStateError


class MissingResumeService:
    def load_full(self, session_id: str) -> object:
        raise SessionStateError(f"未找到会话 {session_id}。")


@dataclass(frozen=True)
class CurrentSnapshot:
    session_id: str


class CurrentSession:
    def current(self) -> CurrentSnapshot:
        return CurrentSnapshot("current")


def _runtime_without_disk_state() -> ProjectRuntime:
    runtime = object.__new__(ProjectRuntime)
    runtime.resume_service = MissingResumeService()  # type: ignore[assignment]
    runtime.session = CurrentSession()  # type: ignore[assignment]
    return runtime


def test_current_unpersisted_session_has_an_empty_transcript() -> None:
    assert _runtime_without_disk_state().transcript("current") == ()


def test_unknown_session_is_not_misreported_as_the_current_empty_session() -> None:
    with pytest.raises(SessionStateError, match="missing"):
        _runtime_without_disk_state().transcript("missing")
