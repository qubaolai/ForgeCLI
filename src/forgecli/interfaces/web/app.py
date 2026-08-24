"""FastAPI 本地控制面及版本化 Web API。"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from forgecli.application.llm import providers as provider_registry
from forgecli.application.planning.plan_review import PlanReviewChoice
from forgecli.application.security.workspace_grants import GrantError
from forgecli.domain.agent.run_events import AgentRunEventKind
from forgecli.domain.config import config_keys
from forgecli.domain.intents import SessionMode
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.thinking import ThinkingEffortName, ThinkingMode
from forgecli.domain.workspace.project import WorkspaceError
from forgecli.infrastructure.project import ProjectLockedError
from forgecli.interfaces.web.events import WebEventHub
from forgecli.interfaces.web.runtime import (
    ProjectRuntime,
    ProjectRuntimeRegistry,
    build_project_service,
)
from forgecli.interfaces.web.serialization import to_jsonable
from forgecli.shared import __version__
from forgecli.shared.errors import SessionStateError

_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
_SESSION_COOKIE = "forge_web_session"
_CSRF_COOKIE = "forge_web_csrf"
_OPEN_PATHS = {"/boot", "/api/v1/health"}
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    ),
}
_STREAM_KEEP_ALIVE_SECONDS = 15.0
# 构建产物带内容哈希, 可以长期缓存; 入口 HTML 必须每次回源, 否则升级 Forge 之后浏览器
# 还在跑上一版前端 —— 那是一类很难自查的故障: 代码改了, 用户看到的行为没改。
_IMMUTABLE_PREFIX = "/assets/"
_ASSET_CACHE = "public, max-age=31536000, immutable"
_DOCUMENT_CACHE = "no-cache"
_MAX_RESTORED_OUTPUT = 4000
# 直接用浏览器打开陈旧地址时给出可操作的说明，而不是一行裸 "Unauthorized"。
_UNAUTHORIZED_PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Forge · 本地会话已失效</title>
<style>
:root { color-scheme: light dark; }
body { margin: 0; min-height: 100vh; display: grid; place-items: center;
  padding: 32px; background: #0d0f13; color: #e7e9ee;
  font-family: Inter, ui-sans-serif, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 460px; }
h1 { margin: 0 0 12px; font-size: 20px; }
p { margin: 0 0 10px; color: #9198a7; line-height: 1.75; font-size: 14px; }
code { padding: 2px 6px; border-radius: 5px; background: #e8ebef; color: #111827;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .92em; }
@media (prefers-color-scheme: light) {
  body { background: #f4f5f7; color: #1c2430; }
  p { color: #687182; }
}
</style></head>
<body><main>
<h1>本地会话已失效</h1>
<p>Forge 每次启动都会换用新的一次性启动令牌，之前的地址不再有效。</p>
<p>请回到运行 <code>forge</code> 的终端，复制那里打印的启动链接重新打开控制面。</p>
</main></body></html>
"""


class TrustProjectRequest(BaseModel):
    path: str = Field(min_length=1)


class MessageRequest(BaseModel):
    text: str = Field(min_length=1)


class ApprovalDecisionRequest(BaseModel):
    decision: str


class PlanReviewRequest(BaseModel):
    decision: str
    note: str = ""


class ModeRequest(BaseModel):
    mode: SessionMode


class ConfigUpdateRequest(BaseModel):
    value: str = Field(min_length=1)


class WorkspaceGrantRequest(BaseModel):
    path: str = Field(min_length=1)
    access: str = "read"


class RestoreRequest(BaseModel):
    force_conflicts: bool = False


class CurrentModelRequest(BaseModel):
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)


class ModelOverrideRequest(BaseModel):
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)


class ThinkingRequest(BaseModel):
    mode: str = ""
    effort: str = ""


class ModelCreateRequest(BaseModel):
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    params: dict[str, object] = Field(default_factory=dict)


class LlmFieldUpdateRequest(BaseModel):
    value: str = ""


class _SecurityState:
    def __init__(
        self,
        boot_token: str | None = None,
        *,
        session: str | None = None,
        csrf: str | None = None,
    ) -> None:
        self.boot_token = boot_token or secrets.token_urlsafe(32)
        # 会话与 CSRF 密钥可以由启动器跨重启复用：否则每次重启都会把已经打开的
        # 浏览器窗口踢下线，自动重连也就没有意义了。
        self.session = session or secrets.token_urlsafe(32)
        self.csrf = csrf or secrets.token_urlsafe(24)


class LocalControlPlaneGuard:
    """本地控制面的 Host/会话/CSRF 校验与安全响应头。

    必须是纯 ASGI 中间件：``BaseHTTPMiddleware`` 会把响应体搬进内存流再转发，SSE 长连接
    因此既看不到客户端断连，也会在服务退出时留下一串 ``CancelledError`` 栈。
    """

    def __init__(self, app: ASGIApp, security: _SecurityState) -> None:
        self.app = app
        self.security = security

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        denial = self._denial(Request(scope))
        if denial is not None:
            denial.headers.update(_SECURITY_HEADERS)
            await denial(scope, receive, send)
            return

        path = str(scope.get("path", ""))

        async def send_hardened(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _SECURITY_HEADERS.items():
                    headers[name] = value
                if "cache-control" not in headers:
                    headers["Cache-Control"] = (
                        _ASSET_CACHE
                        if path.startswith(_IMMUTABLE_PREFIX)
                        else _DOCUMENT_CACHE
                    )
            await send(message)

        await self.app(scope, receive, send_hardened)

    def _denial(self, request: Request) -> Response | None:
        if request.url.hostname not in _ALLOWED_HOSTS:
            return Response("Invalid Host", status_code=status.HTTP_400_BAD_REQUEST)
        if (
            request.url.path not in _OPEN_PATHS
            and request.cookies.get(_SESSION_COOKIE) != self.security.session
        ):
            if "text/html" in request.headers.get("accept", ""):
                return HTMLResponse(
                    _UNAUTHORIZED_PAGE, status_code=status.HTTP_401_UNAUTHORIZED
                )
            return Response("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)
        if request.method not in _WRITE_METHODS:
            return None
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if origin and origin != expected:
            return Response("Invalid Origin", status_code=status.HTTP_403_FORBIDDEN)
        if request.url.path != "/boot" and (
            request.cookies.get(_CSRF_COOKIE) != self.security.csrf
            or request.headers.get("x-csrf-token") != self.security.csrf
        ):
            return Response("Invalid CSRF token", status_code=status.HTTP_403_FORBIDDEN)
        return None


def _frame(cursor: int, name: str, data: str) -> str:
    return f"id: {cursor}\nevent: {name}\ndata: {data}\n\n"


def _turn_snapshots(hub: WebEventHub) -> list[dict[str, object]]:
    """把缓冲事件按 turn 归档，并把流式增量折叠成每次模型调用的一段正文。

    逐条返回 ``model_output_delta`` 会让一次刷新拖回几百 KB，页面还要再拼一遍；
    折叠之后一个长 turn 也只剩几十条事件。
    """
    turns: dict[str, dict[str, object]] = {}
    for item in hub.buffered():
        turn_id = str(item.data.get("turn_id", ""))
        entry = turns.setdefault(
            turn_id, {"turn_id": turn_id, "events": [], "outputs": {}}
        )
        if item.kind != AgentRunEventKind.MODEL_OUTPUT_DELTA.value:
            events = entry["events"]
            assert isinstance(events, list)
            events.append(item.data)
            continue
        payload = item.data.get("payload")
        text = payload.get("text", "") if isinstance(payload, dict) else ""
        outputs = entry["outputs"]
        assert isinstance(outputs, dict)
        key = str(item.data.get("request_id") or "")
        outputs[key] = f"{outputs.get(key, '')}{text}"[:_MAX_RESTORED_OUTPUT]
    return list(turns.values())


def _project_payload(project: object) -> dict[str, object]:
    payload = to_jsonable(project)
    assert isinstance(payload, dict)
    return payload


def _runtime(request: Request) -> ProjectRuntime:
    registry: ProjectRuntimeRegistry = request.app.state.registry
    runtime = registry.active
    if runtime is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "尚未激活项目")
    return runtime


def _workspace_access(runtime: ProjectRuntime, root: str) -> str:
    if root == runtime.context.project.primary_workspace_root:
        return "write"
    grant = runtime.tools.grants.access_for(root)
    return "read" if grant is None else grant.value


def create_app(
    *,
    registry: ProjectRuntimeRegistry | None = None,
    boot_token: str | None = None,
    session_token: str | None = None,
    csrf_token: str | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    runtimes = registry or ProjectRuntimeRegistry(build_project_service())
    security = _SecurityState(boot_token, session=session_token, csrf=csrf_token)
    # 服务停止信号：由启动器在等待连接收尾之前置位，长连接据此主动收尾。
    stopping = asyncio.Event()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            stopping.set()
            # close() 要 join Agent 线程并释放项目锁，放进线程里不阻塞事件循环。
            await asyncio.to_thread(runtimes.close)

    app = FastAPI(
        title="Forge Local Web API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.registry = runtimes
    app.state.security = security
    app.state.stopping = stopping
    app.add_middleware(LocalControlPlaneGuard, security=security)

    @app.exception_handler(WorkspaceError)
    async def workspace_error(_request: Request, exc: WorkspaceError) -> JSONResponse:
        return JSONResponse(
            {"detail": exc.message}, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY
        )

    @app.exception_handler(GrantError)
    async def grant_error(_request: Request, exc: GrantError) -> JSONResponse:
        return JSONResponse(
            {"detail": exc.message}, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY
        )

    @app.get("/boot", include_in_schema=False)
    async def boot(token: str) -> Response:
        if not secrets.compare_digest(token, security.boot_token):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "启动令牌无效")
        security.boot_token = ""
        response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(
            _SESSION_COOKIE,
            security.session,
            httponly=True,
            samesite="strict",
            secure=False,
        )
        response.set_cookie(
            _CSRF_COOKIE,
            security.csrf,
            httponly=False,
            samesite="strict",
            secure=False,
        )
        return response

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/v1/bootstrap")
    async def bootstrap(request: Request) -> dict[str, object]:
        runtime = request.app.state.registry.active
        return {
            "version": __version__,
            "active_project_id": (
                None if runtime is None else runtime.project.project_id
            ),
            "csrf_token": security.csrf,
        }

    @app.get("/api/v1/projects")
    async def projects(request: Request) -> dict[str, object]:
        project_service = request.app.state.registry.projects
        active = request.app.state.registry.active
        return {
            "items": [
                _project_payload(project) for project in project_service.list_trusted()
            ],
            "active_project_id": (
                None if active is None else active.project.project_id
            ),
        }

    @app.post("/api/v1/projects", status_code=status.HTTP_201_CREATED)
    async def trust_project(
        body: TrustProjectRequest, request: Request
    ) -> dict[str, object]:
        project_service = request.app.state.registry.projects
        path = project_service.normalize_workspace_dir(body.path, Path.cwd())
        existing = project_service.find_trusted(path)
        project = existing or project_service.trust(path)
        return _project_payload(project)

    @app.post("/api/v1/projects/{project_id}/activate")
    async def activate_project(project_id: str, request: Request) -> dict[str, object]:
        try:
            runtime = request.app.state.registry.activate(project_id)
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "项目不存在") from None
        except ProjectLockedError as exc:
            raise HTTPException(status.HTTP_423_LOCKED, exc.message) from exc
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return _project_payload(runtime.project)

    @app.get("/api/v1/sessions")
    async def list_sessions(request: Request) -> dict[str, object]:
        runtime = _runtime(request)
        return {
            "current_session_id": runtime.session.current().session_id,
            "current_session": to_jsonable(runtime.session.current()),
            "items": [to_jsonable(item) for item in runtime.list_sessions()],
        }

    @app.post("/api/v1/sessions", status_code=status.HTTP_201_CREATED)
    async def new_session(request: Request) -> object:
        try:
            return to_jsonable(_runtime(request).new_session())
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @app.post("/api/v1/sessions/{session_id}/resume")
    async def resume_session(session_id: str, request: Request) -> object:
        try:
            return to_jsonable(_runtime(request).resume(session_id))
        except SessionStateError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @app.get("/api/v1/sessions/{session_id}/transcript")
    async def transcript(session_id: str, request: Request) -> dict[str, object]:
        try:
            items = _runtime(request).transcript(session_id)
        except SessionStateError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc
        return {"items": [to_jsonable(event) for event in items]}

    @app.post("/api/v1/turns", status_code=status.HTTP_202_ACCEPTED)
    async def start_turn(body: MessageRequest, request: Request) -> object:
        try:
            return to_jsonable(_runtime(request).start_turn(body.text))
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @app.get("/api/v1/turns/current")
    async def current_turn(request: Request) -> object:
        return to_jsonable(_runtime(request).current_run())

    @app.post("/api/v1/turns/current/cancel", status_code=status.HTTP_202_ACCEPTED)
    async def cancel_turn(request: Request) -> dict[str, bool]:
        return {"cancel_requested": _runtime(request).cancel()}

    @app.get("/api/v1/events")
    async def events(
        request: Request,
        after: str | None = None,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        hub = _runtime(request).events
        # 只有断线重连才补发历史；首次连接从当前位置开始，已完成 turn 的正文由
        # transcript 提供，不能靠重放事件缓冲拼出来。页面自己管重连节奏，所以除了
        # EventSource 自带的 Last-Event-ID，也接受显式的 after 查询参数。
        resume = after if after is not None else last_event_id
        try:
            cursor = hub.cursor if resume is None else int(resume)
        except ValueError:
            cursor = hub.cursor

        async def stream() -> AsyncIterator[str]:
            """一条 SSE 流；服务停止或客户端断开时立即收尾，不拖住优雅退出。"""
            nonlocal cursor
            while not stopping.is_set():
                if await request.is_disconnected():
                    return
                resync, pending = hub.after(cursor)
                if resync:
                    cursor = hub.cursor
                    yield _frame(
                        cursor,
                        "resync_required",
                        '{"reason":"event_buffer_expired"}',
                    )
                    continue
                if pending:
                    # 一次 yield 送完这批：逐条 yield 会让一次长回答变成上千次小写入。
                    frames = []
                    for item in pending:
                        cursor = item.cursor
                        data = json.dumps(
                            item.data,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        frames.append(_frame(cursor, "run_event", data))
                    yield "".join(frames)
                    continue
                if hub.closed:
                    # 项目被切换或关闭：结束这条流，浏览器会重连到新的 Runtime。
                    return
                await hub.wait(
                    cursor, timeout=_STREAM_KEEP_ALIVE_SECONDS, stop=stopping
                )
                if not stopping.is_set():
                    yield ": keep-alive\n\n"
            # 让页面知道这是本地服务主动停止，而不是需要重连的网络抖动。
            yield _frame(cursor, "server_stopping", '{"reason":"local_shutdown"}')

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/runs")
    async def runs(request: Request) -> dict[str, object]:
        """进程内仍保留的运行事件，按 turn 分组。

        刷新页面后据此重建"处理过程"。它不是恢复真相源 (ADR-0016 §9)：服务重启后为空，
        会话正文仍由 transcript 提供。
        """
        return {"items": _turn_snapshots(_runtime(request).events)}

    @app.get("/api/v1/approvals")
    async def approvals(request: Request) -> dict[str, object]:
        return {"items": list(_runtime(request).approvals.list_pending())}

    @app.post("/api/v1/approvals/{approval_id}/resolve")
    async def resolve_approval(
        approval_id: str, body: ApprovalDecisionRequest, request: Request
    ) -> dict[str, bool]:
        resolved = _runtime(request).approvals.resolve(approval_id, body.decision)
        if not resolved:
            raise HTTPException(status.HTTP_409_CONFLICT, "审批不存在或决议无效")
        return {"resolved": True}

    @app.get("/api/v1/planning")
    async def planning(request: Request) -> object:
        service = _runtime(request).tools.planning
        payload = to_jsonable(service.load())
        assert isinstance(payload, dict)
        payload["markdown"] = service.read_plan()
        return payload

    @app.get("/api/v1/planning/markdown")
    async def planning_markdown(request: Request) -> Response:
        """在浏览器中打开当前计划的本地 Markdown 派生视图。"""
        runtime = _runtime(request)
        active = runtime.tools.planning.load().plan
        markdown = runtime.tools.planning.read_plan()
        if active is None or markdown is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "当前没有活动计划")
        filename = f"{active.plan_id}-r{active.revision}.md"
        return Response(
            content=markdown,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )

    @app.post("/api/v1/plan-reviews/current/resolve")
    async def resolve_plan_review(
        body: PlanReviewRequest, request: Request
    ) -> dict[str, bool]:
        try:
            choice = PlanReviewChoice(body.decision)
        except ValueError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "未知计划决议"
            ) from None
        if not _runtime(request).resolve_plan_review(choice, body.note):
            raise HTTPException(status.HTTP_409_CONFLICT, "当前没有待评审计划")
        return {"resolved": True}

    @app.post("/api/v1/mode")
    async def set_mode(body: ModeRequest, request: Request) -> object:
        try:
            return to_jsonable(_runtime(request).set_mode(body.mode))
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @app.get("/api/v1/settings")
    async def settings(request: Request) -> dict[str, object]:
        runtime = _runtime(request)
        return {
            "items": [
                {
                    "key": item.name,
                    "level": item.level.name.lower(),
                    "kind": item.kind.name.lower(),
                    "value": runtime.config.display(item.name),
                    "choices": list(item.choices),
                }
                for item in config_keys.SCHEMA
            ]
        }

    @app.patch("/api/v1/settings/{key:path}")
    async def update_setting(
        key: str, body: ConfigUpdateRequest, request: Request
    ) -> dict[str, str]:
        runtime = _runtime(request)
        if runtime.busy:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "turn 运行期间不能修改运行配置"
            )
        try:
            runtime.config.set(key, body.value)
        except Exception as exc:  # 配置错误族都带可直接展示的信息
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        return {"key": key, "value": runtime.config.display(key)}

    @app.get("/api/v1/models")
    async def models(request: Request) -> dict[str, object]:
        runtime = _runtime(request)
        cache, circuit_breaker, retry = runtime.llm_config.runtime_settings_snapshot()
        current = runtime.current_model()
        return {
            "items": [to_jsonable(item) for item in runtime.llm_config.providers()],
            "known_providers": [
                {
                    "id": provider_id,
                    "label": spec.label,
                    "api_key_env": spec.api_key_env,
                    # 只看环境变量在不在, 不读它的值 (application/llm/availability).
                    "available": runtime.availability.is_available(provider_id),
                }
                for provider_id, spec in provider_registry.REGISTRY.items()
            ],
            "current_model": "" if current is None else str(current),
            "overrides": runtime.model_overrides(),
            "origins": [origin.value for origin in RequestOrigin],
            "thinking": runtime.thinking_view(),
            "runtime": {
                "cache": to_jsonable(cache),
                "circuit_breaker": to_jsonable(circuit_breaker),
                "retry": to_jsonable(retry),
            },
        }

    @app.put("/api/v1/models/current")
    async def set_current_model(
        body: CurrentModelRequest, request: Request
    ) -> dict[str, str]:
        """选择运行时默认模型 (CLI 的 /model)。两个配置键一起改。"""
        runtime = _runtime(request)
        if runtime.busy:
            raise HTTPException(status.HTTP_409_CONFLICT, "turn 运行期间不能切换模型")
        try:
            runtime.set_current_model(body.provider_id, body.model_id)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        current = runtime.current_model()
        return {"current_model": "" if current is None else str(current)}

    @app.put("/api/v1/model-overrides/{origin}")
    async def set_model_override(
        origin: str, body: ModelOverrideRequest, request: Request
    ) -> dict[str, object]:
        """按用途覆盖当前模型 (CLI 的 /config 用途模型覆盖)。"""
        runtime = _runtime(request)
        if runtime.busy:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "turn 运行期间不能修改模型覆盖"
            )
        try:
            runtime.set_model_override(origin, body.provider_id, body.model_id)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        return {"overrides": runtime.model_overrides()}

    @app.delete("/api/v1/model-overrides/{origin}")
    async def clear_model_override(origin: str, request: Request) -> dict[str, object]:
        runtime = _runtime(request)
        if runtime.busy:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "turn 运行期间不能修改模型覆盖"
            )
        try:
            runtime.clear_model_override(origin)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        return {"overrides": runtime.model_overrides()}

    @app.post("/api/v1/thinking")
    async def update_thinking(
        body: ThinkingRequest, request: Request
    ) -> dict[str, object]:
        """当前模型的 thinking 开关与强度 (CLI 的 /thinking)。只改本进程，不落盘。"""
        runtime = _runtime(request)
        if runtime.busy:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "turn 运行期间不能修改 thinking"
            )
        try:
            changed = runtime.update_thinking(body.mode, body.effort)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        return {"changed": changed, "thinking": runtime.thinking_view()}

    @app.post("/api/v1/models", status_code=status.HTTP_201_CREATED)
    async def add_model(
        body: ModelCreateRequest,
        request: Request,
    ) -> dict[str, bool]:
        runtime = _runtime(request)
        if runtime.busy:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "turn 运行期间不能修改模型配置"
            )
        try:
            runtime.llm_config.add_model(body.provider_id, body.model_id, body.params)
            runtime.reload_llm()
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        return {"created": True}

    @app.delete("/api/v1/models")
    async def remove_model(
        provider_id: str, model_id: str, request: Request
    ) -> dict[str, bool]:
        runtime = _runtime(request)
        if runtime.busy:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "turn 运行期间不能修改模型配置"
            )
        try:
            runtime.llm_config.remove_model(provider_id, model_id)
            runtime.reload_llm()
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        return {"removed": True}

    @app.patch("/api/v1/models/{field}")
    async def update_model_field(
        provider_id: str,
        model_id: str,
        field: str,
        body: LlmFieldUpdateRequest,
        request: Request,
    ) -> dict[str, bool]:
        runtime = _runtime(request)
        if runtime.busy:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "turn 运行期间不能修改模型配置"
            )
        try:
            if field == "extra":
                runtime.llm_config.set_model_extra(provider_id, model_id, body.value)
            elif field == "thinking_mode":
                runtime.llm_config.update_model_thinking(
                    provider_id, model_id, mode=ThinkingMode(body.value)
                )
            elif field == "thinking_effort":
                runtime.llm_config.update_model_thinking(
                    provider_id, model_id, effort=ThinkingEffortName(body.value)
                )
            elif field == "thinking_efforts":
                runtime.llm_config.set_model_thinking_efforts(
                    provider_id, model_id, body.value
                )
            elif field == "thinking_default_effort":
                runtime.llm_config.set_model_thinking_default_effort(
                    provider_id, model_id, body.value
                )
            else:
                runtime.llm_config.set_model_field(
                    provider_id, model_id, field, body.value
                )
            runtime.reload_llm()
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        return {"updated": True}

    @app.patch("/api/v1/providers/{provider_id}/{field}")
    async def update_provider_field(
        provider_id: str,
        field: str,
        body: LlmFieldUpdateRequest,
        request: Request,
    ) -> dict[str, bool]:
        runtime = _runtime(request)
        if runtime.busy:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "turn 运行期间不能修改模型配置"
            )
        try:
            runtime.llm_config.set_provider_field(provider_id, field, body.value)
            runtime.reload_llm()
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        return {"updated": True}

    @app.patch("/api/v1/llm-runtime/{section}/{field}")
    async def update_llm_runtime_field(
        section: str,
        field: str,
        body: LlmFieldUpdateRequest,
        request: Request,
    ) -> dict[str, bool]:
        runtime = _runtime(request)
        if runtime.busy:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "turn 运行期间不能修改网关配置"
            )
        try:
            runtime.llm_config.set_runtime_field(section, field, body.value)
            runtime.reload_llm()
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        return {"updated": True}

    @app.get("/api/v1/runtime")
    async def runtime_profile(request: Request) -> dict[str, object]:
        runtime = _runtime(request)
        return {
            "profile": to_jsonable(runtime.tools.profile),
            "gateway_metrics": to_jsonable(runtime.llm.gateway_metrics.snapshot()),
        }

    @app.get("/api/v1/status")
    async def status_view(request: Request) -> dict[str, object]:
        """会话, 模式, 当前模型与可操作目录 (CLI 的 /status)。"""
        return _runtime(request).status()

    @app.get("/api/v1/recovery")
    async def recovery_status(request: Request) -> dict[str, object]:
        """恢复层状态与未收尾事务 (CLI 的 /recovery)。"""
        return _runtime(request).recovery_status()

    @app.get("/api/v1/plans")
    async def plans(request: Request) -> object:
        """计划目录与活动指针 (CLI 的 /plan-doc list)。"""
        return to_jsonable(_runtime(request).plan_index())

    @app.post("/api/v1/plans/{plan_id}/activate")
    async def activate_plan(plan_id: str, request: Request) -> dict[str, bool]:
        """切换活动计划 (CLI 的 /plan-doc use <id>)。"""
        if not _runtime(request).activate_plan(plan_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "计划不存在")
        return {"activated": True}

    @app.get("/api/v1/tools")
    async def tools(request: Request) -> dict[str, object]:
        runtime = _runtime(request)
        catalog = runtime.tools.dispatcher.catalog_for(runtime.session.current().mode)
        return {"items": [to_jsonable(item) for item in catalog.entries]}

    @app.get("/api/v1/workspace-roots")
    async def workspace_roots(request: Request) -> dict[str, object]:
        runtime = _runtime(request)
        return {
            "primary": runtime.context.project.primary_workspace_root,
            "items": [
                {
                    "path": root,
                    "access": _workspace_access(runtime, root),
                }
                for root in runtime.context.project.workspace_roots
            ],
        }

    @app.post("/api/v1/workspace-roots", status_code=status.HTTP_201_CREATED)
    async def add_workspace_root(
        body: WorkspaceGrantRequest, request: Request
    ) -> dict[str, object]:
        runtime = _runtime(request)
        if runtime.busy:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "turn 运行期间不能修改工作区授权"
            )
        if body.access not in {"read", "write"}:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "access 只能是 read/write"
            )
        projects_service = request.app.state.registry.projects
        path = projects_service.normalize_workspace_dir(
            body.path, Path(runtime.context.project.primary_workspace_root)
        )
        if str(path) != runtime.context.project.primary_workspace_root:
            runtime.grant_workspace(path, write=body.access == "write")
        updated = projects_service.add_workspace_dir(runtime.context.project, path)
        runtime.context.project = updated
        runtime.project = updated
        return _project_payload(updated)

    @app.delete("/api/v1/workspace-roots")
    async def remove_workspace_root(path: str, request: Request) -> dict[str, object]:
        runtime = _runtime(request)
        if runtime.busy:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "turn 运行期间不能修改工作区授权"
            )
        projects_service = request.app.state.registry.projects
        normalized = projects_service.normalize_workspace_dir(
            path, Path(runtime.context.project.primary_workspace_root)
        )
        updated = projects_service.remove_workspace_dir(
            runtime.context.project, normalized
        )
        runtime.revoke_workspace(normalized)
        runtime.context.project = updated
        runtime.project = updated
        return _project_payload(updated)

    @app.get("/api/v1/rules")
    async def rules(request: Request) -> dict[str, object]:
        runtime = _runtime(request)
        return {
            "items": [
                to_jsonable(rule)
                for rule in runtime.tools.learned.rules
                if rule.match.workspace_id == runtime.tools.workspace_id
            ]
        }

    @app.delete("/api/v1/rules/{rule_id}")
    async def revoke_rule(rule_id: str, request: Request) -> dict[str, bool]:
        if not _runtime(request).tools.learned.revoke(rule_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "学习规则不存在")
        return {"revoked": True}

    @app.post("/api/v1/rules/prune")
    async def prune_rules(request: Request) -> dict[str, int]:
        return {"removed": _runtime(request).tools.learned.prune()}

    @app.get("/api/v1/checkpoints")
    async def checkpoints(request: Request) -> dict[str, object]:
        runtime = _runtime(request)
        return {"items": [to_jsonable(item) for item in runtime.list_checkpoints()]}

    @app.get("/api/v1/checkpoints/{checkpoint_id}/preview")
    async def preview_checkpoint(checkpoint_id: str, request: Request) -> object:
        runtime = _runtime(request)
        checkpoint = runtime.checkpoint(checkpoint_id)
        if checkpoint is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "恢复点不存在")
        return to_jsonable(
            runtime.tools.recovery.preview(checkpoint, runtime.tools.context_factory())
        )

    @app.post("/api/v1/checkpoints/{checkpoint_id}/restore")
    async def restore_checkpoint(
        checkpoint_id: str, body: RestoreRequest, request: Request
    ) -> object:
        runtime = _runtime(request)
        if runtime.busy:
            raise HTTPException(status.HTTP_409_CONFLICT, "turn 运行期间不能执行恢复")
        checkpoint = runtime.checkpoint(checkpoint_id)
        if checkpoint is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "恢复点不存在")
        return to_jsonable(
            runtime.tools.recovery.restore(
                checkpoint,
                runtime.tools.context_factory(),
                force_conflicts=body.force_conflicts,
            )
        )

    @app.post("/api/v1/undo")
    async def undo_latest(request: Request) -> object:
        runtime = _runtime(request)
        if runtime.busy:
            raise HTTPException(status.HTTP_409_CONFLICT, "turn 运行期间不能执行撤销")
        checkpoints = runtime.list_checkpoints()
        if not checkpoints:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "没有可撤销的操作")
        return to_jsonable(
            runtime.tools.recovery.restore(
                checkpoints[0], runtime.tools.context_factory()
            )
        )

    assets = static_dir or Path(__file__).with_name("static")
    app.mount("/", StaticFiles(directory=assets, html=True), name="web")
    return app
