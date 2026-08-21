"""计划与待办的落盘 (ADR-0022 §2).

目录布局:

    <plans_root>/
      index.json                  活动指针与摘要
      <plan_id>/
        r1.json                   结构化正文, 权威
        r1.md                     按模板渲染, 人读, 派生
      todo/
        current.json
        archive/<todo_id>-r<n>.json

``plan_id`` 与 ``todo_id`` 由模型按任务命名 (ADR-0022 决策 7), 所以目录名本身就说明这份
计划是关于什么的. 命名的规整与去重在 PlanningService 完成, 这里只当它是一个字符串.

**读失败一律降级成空值, 不抛.** 计划坏了不该让人开不了工 (§10). 写失败抛 PlanStoreError
—— 那必须让调用方知道, 因为模型以为自己存下来了.

**不设文件锁.** 项目级 ProcessLock 已保证同一项目同时只有一个 Forge 进程 (§2.3). 写入
仍然原子 (write_document 先写临时文件再 rename), 防的是崩溃, 不是并发.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from forgecli.application.planning.plan_store import PlanStore, PlanStoreError
from forgecli.domain.planning import (
    PlanDocument,
    PlanIndex,
    PlanStatus,
    PlanStep,
    PlanSummary,
    TodoItem,
    TodoList,
    TodoStatus,
)
from forgecli.infrastructure.json_io import read_document, write_document
from forgecli.shared.errors import ConfigReadError

__all__ = ["FsPlanStore"]

_INDEX_SCHEMA_VERSION = 1


class FsPlanStore(PlanStore):
    """根路径**延迟求值**.

    计划目录按会话分区, 而组合根装配这个 store 时还没 session.start() —— 那时读 session
    id 会直接抛 SessionStateError.

    每次调用现算而不是首次调用后缓存: `/resume` 会在同一个进程内换会话, 缓存下来的根路径
    会让 resume 之后的计划仍然写进上一段会话的目录.
    """

    def __init__(self, plans_root: Callable[[], Path]) -> None:
        self._plans_root = plans_root

    @property
    def _root(self) -> Path:
        return self._plans_root()

    # ---- 索引 ----

    def load_index(self) -> PlanIndex:
        document = self._read(self._root / "index.json")
        if document is None:
            return PlanIndex()
        plans: list[PlanSummary] = []
        for raw in _object_list(document, "plans"):
            summary = _summary_of(raw)
            if summary is not None:
                plans.append(summary)
        return PlanIndex(
            active_plan_id=_text(document.get("active_plan_id")),
            active_todo_id=_text(document.get("active_todo_id")),
            plans=tuple(plans),
        )

    def save_index(self, index: PlanIndex) -> None:
        self._write(
            self._root / "index.json",
            {
                "schema_version": _INDEX_SCHEMA_VERSION,
                "active_plan_id": index.active_plan_id,
                "active_todo_id": index.active_todo_id,
                "plans": [
                    {
                        "plan_id": summary.plan_id,
                        "revision": summary.revision,
                        "status": summary.status.value,
                        "title": summary.title,
                        "template_version": summary.template_version,
                        "created_at": summary.created_at,
                        "updated_at": summary.updated_at,
                    }
                    for summary in index.plans
                ],
            },
        )

    # ---- 计划 ----

    def load_plan(
        self, plan_id: str, revision: int | None = None
    ) -> PlanDocument | None:
        directory = _plan_directory(self._root, plan_id)
        if directory is None:
            return None
        target = (
            directory / f"r{revision}.json"
            if revision is not None
            else _latest_revision(directory)
        )
        if target is None:
            return None
        document = self._read(target)
        if document is None:
            return None
        try:
            return _plan_of(plan_id, document)
        except (ValueError, TypeError):
            # 文件在, 但内容不满足不变量 (手改坏了, 或旧版本写的). 按读不到处理.
            return None

    def save_plan(self, plan: PlanDocument, rendered: str) -> None:
        directory = _plan_directory(self._root, plan.plan_id)
        if directory is None:
            raise PlanStoreError(f"非法 plan_id: {plan.plan_id!r}")
        self._write(
            directory / f"r{plan.revision}.json",
            {
                "plan_id": plan.plan_id,
                "revision": plan.revision,
                "template_version": plan.template_version,
                "status": plan.status.value,
                "title": plan.title,
                "goal": plan.goal,
                "context": plan.context,
                "approach": plan.approach,
                "risks": list(plan.risks),
                "acceptance": list(plan.acceptance),
                "created_at": plan.created_at,
                "updated_at": plan.updated_at,
                "steps": [
                    {"title": step.title, "detail": step.detail} for step in plan.steps
                ],
            },
        )
        self._write_text(directory / f"r{plan.revision}.md", rendered)

    # ---- 待办 ----

    def load_todo(self) -> TodoList | None:
        document = self._read(self._root / "todo" / "current.json")
        if document is None:
            return None
        try:
            todo = _todo_of(document)
            if _safe_component(todo.todo_id) is None:
                return None
            if todo.plan_id and _safe_component(todo.plan_id) is None:
                return None
            return todo
        except (ValueError, TypeError):
            return None

    def save_todo(self, todo: TodoList) -> None:
        if _safe_component(todo.todo_id) is None:
            raise PlanStoreError(f"非法 todo_id: {todo.todo_id!r}")
        self._write(self._root / "todo" / "current.json", _todo_document(todo))

    def archive_todo(self, todo: TodoList) -> None:
        todo_id = _safe_component(todo.todo_id)
        if todo_id is None:
            raise PlanStoreError(f"非法 todo_id: {todo.todo_id!r}")
        target = self._root / "todo" / "archive" / f"{todo_id}-r{todo.revision}.json"
        self._write(target, _todo_document(todo))

    # ---- IO ----

    def _read(self, path: Path) -> dict[str, Any] | None:
        """读不到就是没有. 语法坏了也是没有 —— 但那要留下痕迹, 由调用方打诊断."""
        try:
            document = read_document(path)
        except ConfigReadError:
            return None
        return document or None

    def _write(self, path: Path, document: Mapping[str, Any]) -> None:
        try:
            write_document(path, document)
        except OSError as exc:
            raise PlanStoreError(f"写入失败: {path}\n  原因: {exc}") from exc

    def _write_text(self, path: Path, body: str) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(path.name + ".tmp")
            temp.write_text(body, encoding="utf-8")
            temp.replace(path)
        except OSError as exc:
            raise PlanStoreError(f"写入失败: {path}\n  原因: {exc}") from exc


# ---- 解码 ----


def _plan_directory(root: Path, plan_id: str) -> Path | None:
    """存储层的第二道边界：任何上层遗漏都不能让路径逃出 plans_root。"""
    component = _safe_component(plan_id)
    if component is None:
        return None
    resolved_root = root.resolve()
    candidate = (resolved_root / component).resolve()
    return candidate if candidate.parent == resolved_root else None


def _safe_component(value: str) -> str | None:
    if not value or Path(value).is_absolute() or len(Path(value).parts) != 1:
        return None
    return None if value in {".", ".."} else value


def _latest_revision(directory: Path) -> Path | None:
    try:
        candidates = [
            item
            for item in directory.iterdir()
            if item.suffix == ".json" and item.stem.startswith("r")
        ]
    except OSError:
        return None
    numbered = [(number, item) for item in candidates if (number := _revision_of(item))]
    if not numbered:
        return None
    return max(numbered, key=lambda pair: pair[0])[1]


def _revision_of(path: Path) -> int:
    try:
        return int(path.stem[1:])
    except ValueError:
        return 0


def _plan_of(plan_id: str, document: Mapping[str, Any]) -> PlanDocument:
    return PlanDocument(
        plan_id=plan_id,
        revision=_number(document.get("revision"), default=1),
        title=_text(document.get("title")),
        goal=_text(document.get("goal")),
        context=_text(document.get("context")),
        approach=_text(document.get("approach")),
        steps=tuple(
            PlanStep(title=_text(raw.get("title")), detail=_text(raw.get("detail")))
            for raw in _object_list(document, "steps")
        ),
        risks=_strings(document.get("risks")),
        acceptance=_strings(document.get("acceptance")),
        status=_status(_text(document.get("status"))),
        template_version=_number(document.get("template_version"), default=1),
        created_at=_text(document.get("created_at")),
        updated_at=_text(document.get("updated_at")),
    )


def _todo_document(todo: TodoList) -> dict[str, Any]:
    return {
        "todo_id": todo.todo_id,
        "plan_id": todo.plan_id,
        "revision": todo.revision,
        "updated_at": todo.updated_at,
        "items": [
            {"title": item.title, "status": item.status.value} for item in todo.items
        ],
    }


def _todo_of(document: Mapping[str, Any]) -> TodoList:
    return TodoList(
        todo_id=_text(document.get("todo_id")),
        items=tuple(
            TodoItem(
                title=_text(raw.get("title")),
                status=_todo_status(_text(raw.get("status"))),
            )
            for raw in _object_list(document, "items")
        ),
        plan_id=_text(document.get("plan_id")),
        revision=_number(document.get("revision"), default=1),
        updated_at=_text(document.get("updated_at")),
    )


def _summary_of(raw: Mapping[str, object]) -> PlanSummary | None:
    plan_id = _text(raw.get("plan_id"))
    if not plan_id:
        return None
    return PlanSummary(
        plan_id=plan_id,
        revision=_number(raw.get("revision"), default=1),
        status=_status(_text(raw.get("status"))),
        title=_text(raw.get("title")),
        template_version=_number(raw.get("template_version"), default=1),
        created_at=_text(raw.get("created_at")),
        updated_at=_text(raw.get("updated_at")),
    )


def _object_list(source: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    raw = source.get(key)
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _text(value: object) -> str:
    return str(value) if isinstance(value, str) else ""


def _number(value: object, *, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str))


def _status(raw: str) -> PlanStatus:
    """认不出的状态按 proposed 处理: 那是唯一还需要人裁决的档, 方向朝保守."""
    try:
        return PlanStatus(raw)
    except ValueError:
        return PlanStatus.PROPOSED


def _todo_status(raw: str) -> TodoStatus:
    try:
        return TodoStatus(raw)
    except ValueError:
        return TodoStatus.PENDING
