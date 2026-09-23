"""首屏显示值不依赖完整模型/设置接口。"""

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import Mock

from forgecli.web.routers import sessions


@dataclass
class Session:
    session_id: str = "session"


def test_session_response_includes_only_current_presentation(monkeypatch):
    config = Mock()
    config.display.return_value = "dark"
    runtime = SimpleNamespace(
        session=SimpleNamespace(current=lambda: Session()),
        list_sessions=lambda: [Session()],
        config=config,
        current_model=lambda: "provider:model",
        thinking_view=lambda: {"model": "provider:model", "configured": False},
    )
    monkeypatch.setattr(sessions, "active_runtime", lambda request: runtime)

    response = asyncio.run(sessions.list_sessions(Mock()))

    assert response["current_session_id"] == "session"
    assert response["presentation"] == {
        "theme": "dark",
        "current_model": "provider:model",
        "thinking": {"model": "provider:model", "configured": False},
    }
    config.display.assert_called_once_with("output.theme")
    config.value_views.assert_not_called()
