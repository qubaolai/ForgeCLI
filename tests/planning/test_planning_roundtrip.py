"""计划与待办的落盘与加载, 三层一起测.

单独测某一层都会绿: 值对象自己校验通过, store 自己写得出文件, service 自己调得动 store.
真正会坏的是它们之间的接缝 —— 落盘之后读回来还是不是同一份, 以及索引指针有没有跟上.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.planning import (
    PLAN_TEMPLATE_VERSION,
    PlanningService,
    StatusUpdate,
)
from forgecli.domain.planning import PlanStatus, PlanStep, TodoStatus
from forgecli.infrastructure.planning import FsPlanStore


@pytest.fixture
def service(tmp_path: Path) -> PlanningService:
    return PlanningService(FsPlanStore(tmp_path / "plans"))


def _write(service: PlanningService, **overrides: object) -> object:
    base: dict[str, object] = {
        "title": "拆分值域对象",
        "goal": "把混在一起的领域概念分开",
        "context": "domain 下几个模块互相引用",
        "approach": "按聚合边界切",
        "steps": (PlanStep(title="读现状"), PlanStep(title="切分", detail="先动 tool")),
        "risks": ("可能碰到循环 import",),
        "acceptance": ("make ci 全绿",),
    }
    base.update(overrides)
    return service.write_plan(**base)  # type: ignore[arg-type]


# ---- 计划往返 ----


def test_a_written_plan_reads_back_identical(service: PlanningService) -> None:
    written = _write(service)

    loaded = service.load().plan

    assert loaded == written


def test_a_new_plan_becomes_the_active_one(service: PlanningService) -> None:
    written = _write(service)

    assert service.load().plan is not None
    assert service.read_plan() is not None
    assert service.read_plan(written.plan_id) == service.read_plan()  # type: ignore[attr-defined]


def test_read_plan_returns_the_rendered_markdown(service: PlanningService) -> None:
    """模型看到的与人看到的是同一份.

    返回结构体会让"你批准的那份"和"模型以为的那份"成为两个东西.
    """
    _write(service)

    body = service.read_plan() or ""

    assert body.startswith("# 拆分值域对象")
    assert "## 步骤" in body
    assert "1. 读现状" in body


def test_the_template_keeps_empty_sections(service: PlanningService) -> None:
    """空小节保留标题写 (无): 删掉整节会让不同计划的结构不一致."""
    _write(service, risks=(), acceptance=())

    body = service.read_plan() or ""

    assert "## 风险\n\n(无)" in body
    assert "## 验收标准\n\n(无)" in body


def test_a_second_write_under_the_same_id_bumps_the_revision(
    service: PlanningService,
) -> None:
    """一次"补充 -> 重提"的往返要留在同一份计划的历史里."""
    first = _write(service)

    second = _write(service, plan_id=first.plan_id, title="拆分值域对象 (修订)")  # type: ignore[attr-defined]

    assert second.plan_id == first.plan_id  # type: ignore[attr-defined]
    assert second.revision == 2  # type: ignore[attr-defined]
    assert second.created_at == first.created_at  # type: ignore[attr-defined]


def test_both_revisions_stay_on_disk(service: PlanningService, tmp_path: Path) -> None:
    first = _write(service)
    _write(service, plan_id=first.plan_id)  # type: ignore[attr-defined]

    directory = tmp_path / "plans" / first.plan_id  # type: ignore[attr-defined]
    assert (directory / "r1.toml").exists()
    assert (directory / "r2.toml").exists()
    assert (directory / "r1.md").exists()


def test_a_rejected_plan_stops_being_active(service: PlanningService) -> None:
    """指针继续指着一份作废的计划, 下次会话开头就会显示它."""
    written = _write(service)

    service.set_plan_status(written.plan_id, PlanStatus.REJECTED)  # type: ignore[attr-defined]

    assert service.load().plan is None


def test_an_approved_plan_stays_active(service: PlanningService) -> None:
    written = _write(service)

    service.set_plan_status(written.plan_id, PlanStatus.APPROVED)  # type: ignore[attr-defined]

    loaded = service.load().plan
    assert loaded is not None
    assert loaded.status is PlanStatus.APPROVED


# ---- 待办 ----


def test_approving_a_plan_seeds_the_todo(service: PlanningService) -> None:
    """批准一份计划 = 用它的步骤播种待办.

    让模型重新把步骤抄一遍, 抄的过程正是步骤悄悄走样的地方.
    """
    written = _write(service)

    todo = service.seed_from_plan(written)  # type: ignore[arg-type]

    assert [item.title for item in todo.items] == ["读现状", "切分"]
    assert todo.plan_id == written.plan_id  # type: ignore[attr-defined]


def test_a_todo_survives_a_reload(service: PlanningService) -> None:
    service.write_todo(["先读", "再写"])

    loaded = service.load().todo

    assert loaded is not None
    assert [item.title for item in loaded.items] == ["先读", "再写"]


def test_status_updates_persist(service: PlanningService) -> None:
    service.write_todo(["先读", "再写"])

    service.update_status([StatusUpdate(index=0, status=TodoStatus.DONE)])

    loaded = service.load().todo
    assert loaded is not None
    assert loaded.items[0].status is TodoStatus.DONE


def test_rewriting_the_table_archives_the_old_one(
    service: PlanningService, tmp_path: Path
) -> None:
    """换一份清单不该让上一份消失: 事后要能回答"当时那份是什么样"."""
    service.write_todo(["先读"])

    service.write_todo(["先读", "再写"])

    archive = tmp_path / "plans" / "todo" / "archive"
    assert list(archive.glob("*.toml"))


def test_rewriting_resets_statuses(service: PlanningService) -> None:
    """整表替换表达的是"步骤拆错了", 保留状态需要按标题配对新旧两表 —— 而标题恰恰是
    这次要改的东西."""
    service.write_todo(["先读"])
    service.update_status([StatusUpdate(index=0, status=TodoStatus.DONE)])

    service.write_todo(["换个说法"])

    loaded = service.load().todo
    assert loaded is not None
    assert loaded.items[0].status is TodoStatus.PENDING


def test_a_todo_can_exist_without_a_plan(service: PlanningService) -> None:
    """绝大多数中等任务直接从一段需求生成待办, 不经过计划评审."""
    service.write_todo(["直接开干"])

    active = service.load()
    assert active.plan is None
    assert active.todo is not None


# ---- 降级 ----


def test_a_missing_directory_is_not_an_error(service: PlanningService) -> None:
    """计划不可用不阻塞主流程."""
    active = service.load()

    assert active.empty
    assert active.diagnostics == ()


def test_a_corrupt_index_degrades_to_no_plan(tmp_path: Path) -> None:
    root = tmp_path / "plans"
    root.mkdir(parents=True)
    (root / "index.toml").write_text("这不是 = = toml", encoding="utf-8")

    assert PlanningService(FsPlanStore(root)).load().empty


def test_an_index_pointing_at_a_missing_plan_says_so(
    service: PlanningService, tmp_path: Path
) -> None:
    written = _write(service)
    for item in (tmp_path / "plans" / written.plan_id).glob("*"):  # type: ignore[attr-defined]
        item.unlink()

    active = service.load()

    assert active.plan is None
    assert any("读不到" in line for line in active.diagnostics)


def test_a_future_template_version_is_refused(
    service: PlanningService, tmp_path: Path
) -> None:
    """比当前实现更新的模板渲染出来大概率缺小节. 与其给人看半截的, 不如说清楚."""
    written = _write(service)
    path = tmp_path / "plans" / written.plan_id / "r1.toml"  # type: ignore[attr-defined]
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            f"template_version = {PLAN_TEMPLATE_VERSION}",
            f"template_version = {PLAN_TEMPLATE_VERSION + 9}",
        ),
        encoding="utf-8",
    )

    active = service.load()

    assert active.plan is None
    assert any("模板版本" in line for line in active.diagnostics)
