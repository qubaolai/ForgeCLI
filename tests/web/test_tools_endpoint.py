"""`GET /api/v1/tools` 发两份清单, 因为界面上有两个问题.

`items` 按当前模式过滤, 管理面板照这个语义展示. `all` 与模式无关, 运行过程视图用它把
历史事件里的工具名翻成中文 —— 那些调用可能发生在换模式之前, 按当前模式过滤的那份里
根本没有它们.

前端曾经为此自带一张手抄表, 于是 `artifact_read` 在后端叫"读回已归档的输出", 在界面上
叫"读取产物". 这组用例钉的就是"前端不必再抄一份".
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.catalog import ToolCatalog
from forgecli.domain.tool.spec import (
    TargetDeclarationAbility,
    ToolAction,
    ToolSpec,
)
from forgecli.interfaces.web.app import create_app


def _spec(name: str, *, capability: Capability, action: ToolAction) -> ToolSpec:
    return ToolSpec(
        name=name,
        version="1",
        title=f"{name} 的用途",
        description="",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        declared_capabilities=frozenset({capability}),
        target_declaration_ability=TargetDeclarationAbility.STATIC,
        default_timeout_seconds=1.0,
        action=action,
    )


_READ = _spec("zz_read", capability=Capability.WORKSPACE_READ, action=ToolAction.READ)
_WRITE = _spec(
    "aa_write", capability=Capability.WORKSPACE_WRITE, action=ToolAction.WRITE
)


class _FakeRuntime:
    """只回答这个路由问的那两件事: 当前模式的目录, 以及全量注册表."""

    def __init__(self) -> None:
        self.busy = False
        self.project = SimpleNamespace(project_id="demo")
        self.session = SimpleNamespace(
            current=lambda: SimpleNamespace(mode="plan", session_id="s1")
        )
        self.tools = SimpleNamespace(
            dispatcher=SimpleNamespace(
                catalog_for=lambda mode: ToolCatalog(
                    entries=(_READ,), reason_tag="mode:plan"
                )
            ),
            registry=SimpleNamespace(describe_all=lambda: (_WRITE, _READ)),
        )


class _FakeRegistry:
    def __init__(self, active: object) -> None:
        self.projects = SimpleNamespace(list_trusted=tuple)
        self.active = active

    def close(self) -> None:
        return None


def _client(tmp_path) -> TestClient:
    app = create_app(
        registry=_FakeRegistry(_FakeRuntime()),  # type: ignore[arg-type]
        boot_token="known-token",
        static_dir=tmp_path,
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    client.get("/boot?token=known-token", follow_redirects=False)
    return client


def test_the_display_listing_carries_tools_the_current_mode_hides(tmp_path) -> None:
    body = _client(tmp_path).get("/api/v1/tools").json()

    assert [item["name"] for item in body["items"]] == ["zz_read"]
    assert [item["name"] for item in body["all"]] == ["aa_write", "zz_read"]


def test_the_display_listing_gives_the_title_and_action_the_backend_owns(
    tmp_path,
) -> None:
    """展示名与动作都从 spec 来: 前端照抄一份就是在等它漂."""
    body = _client(tmp_path).get("/api/v1/tools").json()

    assert body["all"][1] == {
        "name": "zz_read",
        "title": "zz_read 的用途",
        "action": "read",
    }
