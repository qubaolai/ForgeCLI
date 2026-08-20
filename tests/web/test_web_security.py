from dataclasses import dataclass
from types import SimpleNamespace

from fastapi.testclient import TestClient

from forgecli.interfaces.web.app import create_app


@dataclass
class FakeProjects:
    def list_trusted(self) -> tuple[object, ...]:
        return ()


class FakeRegistry:
    def __init__(self, active: object | None = None) -> None:
        self.projects = FakeProjects()
        self.active = active
        self.closed = False

    def close(self) -> None:
        self.closed = True


@dataclass(frozen=True)
class FakePlan:
    plan_id: str
    revision: int


@dataclass(frozen=True)
class FakeActivePlanning:
    plan: FakePlan


def test_boot_token_establishes_same_site_session(tmp_path) -> None:
    registry = FakeRegistry()
    app = create_app(
        registry=registry,  # type: ignore[arg-type]
        boot_token="known-token",
        static_dir=tmp_path,
    )

    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/bootstrap").status_code == 401
        response = client.get("/boot?token=known-token", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        assert (
            client.get("/boot?token=known-token", follow_redirects=False).status_code
            == 401
        )
        bootstrap = client.get("/api/v1/bootstrap")
        assert bootstrap.status_code == 200
        assert bootstrap.json()["csrf_token"]

    assert registry.closed is True


def test_writes_require_matching_csrf_token(tmp_path) -> None:
    app = create_app(
        registry=FakeRegistry(),  # type: ignore[arg-type]
        boot_token="known-token",
        static_dir=tmp_path,
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.get("/boot?token=known-token", follow_redirects=False)
        assert client.post("/api/v1/sessions").status_code == 403
        token = client.get("/api/v1/bootstrap").json()["csrf_token"]
        response = client.post("/api/v1/sessions", headers={"X-CSRF-Token": token})
        assert response.status_code == 409


def test_control_plane_rejects_non_loopback_host(tmp_path) -> None:
    app = create_app(
        registry=FakeRegistry(),  # type: ignore[arg-type]
        static_dir=tmp_path,
    )
    with TestClient(app, base_url="http://evil.example") as client:
        assert client.get("/api/v1/health").status_code == 400


def test_current_plan_markdown_opens_as_inline_local_document(tmp_path) -> None:
    planning = SimpleNamespace(
        load=lambda: FakeActivePlanning(plan=FakePlan("plan_demo", 3)),
        read_plan=lambda: "# Demo\n\n## 步骤\n\n1. 验证 Web\n",
    )
    runtime = SimpleNamespace(tools=SimpleNamespace(planning=planning))
    app = create_app(
        registry=FakeRegistry(runtime),  # type: ignore[arg-type]
        boot_token="known-token",
        static_dir=tmp_path,
    )

    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.get("/boot?token=known-token", follow_redirects=False)
        planning_response = client.get("/api/v1/planning")
        response = client.get("/api/v1/planning/markdown")

    assert planning_response.status_code == 200
    assert planning_response.json()["markdown"].startswith("# Demo")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["content-disposition"] == (
        'inline; filename="plan_demo-r3.md"'
    )
    assert response.text.startswith("# Demo")


def test_app_shell_is_revalidated_while_hashed_assets_are_immutable(tmp_path) -> None:
    """回归: 入口 HTML 被缓存 = 升级 Forge 之后浏览器还在跑上一版前端。

    这类故障很难自查 —— 代码改了, 行为没改, 而两边看起来都对。
    """
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-abc123.js").write_text("export default 1;", encoding="utf-8")
    app = create_app(
        registry=FakeRegistry(),  # type: ignore[arg-type]
        boot_token="known-token",
        static_dir=tmp_path,
    )

    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.get("/boot?token=known-token", follow_redirects=False)
        shell = client.get("/")
        asset = client.get("/assets/index-abc123.js")

    assert shell.headers["cache-control"] == "no-cache"
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
