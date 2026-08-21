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
    return PlanningService(FsPlanStore(lambda: tmp_path / "plans"))


def _write(service: PlanningService, **overrides: object) -> object:
    base: dict[str, object] = {
        "name": "拆分值域对象",
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


@pytest.mark.parametrize("plan_id", ["../escaped", "/tmp/escaped", "nested/plan"])
def test_plan_id_can_never_escape_the_plan_store(
    service: PlanningService, plan_id: str
) -> None:
    with pytest.raises(ValueError, match="plan_id"):
        _write(service, plan_id=plan_id)


def test_revision_requires_an_existing_plan(service: PlanningService) -> None:
    with pytest.raises(ValueError, match="不存在"):
        _write(service, plan_id="missing-plan")


def test_both_revisions_stay_on_disk(service: PlanningService, tmp_path: Path) -> None:
    first = _write(service)
    _write(service, plan_id=first.plan_id)  # type: ignore[attr-defined]

    directory = tmp_path / "plans" / first.plan_id  # type: ignore[attr-defined]
    assert (directory / "r1.json").exists()
    assert (directory / "r2.json").exists()
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
    assert list(archive.glob("*.json"))


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
    (root / "index.json").write_text("这不是 } { json", encoding="utf-8")

    assert PlanningService(FsPlanStore(lambda: root)).load().empty


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
    path = tmp_path / "plans" / written.plan_id / "r1.json"  # type: ignore[attr-defined]
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            f'"template_version": {PLAN_TEMPLATE_VERSION}',
            f'"template_version": {PLAN_TEMPLATE_VERSION + 9}',
        ),
        encoding="utf-8",
    )

    active = service.load()

    assert active.plan is None
    assert any("模板版本" in line for line in active.diagnostics)


# ---- 命名 ----


def test_the_plan_id_comes_from_the_name_the_model_picked(
    service: PlanningService, tmp_path: Path
) -> None:
    """目录名要能说明这份计划是关于什么的, 而不是一串 pl_a3f19c."""
    written = _write(service, name="修复 Web 退出卡住")

    assert written.plan_id == "修复-web-退出卡住"  # type: ignore[attr-defined]
    assert (tmp_path / "plans" / "修复-web-退出卡住" / "r1.json").exists()


def test_a_second_plan_with_the_same_name_gets_a_suffix(
    service: PlanningService,
) -> None:
    """id 是目录名: 撞名直接覆盖等于丢掉上一份计划的全部历史."""
    first = _write(service, name="同一个名字")
    second = _write(service, name="同一个名字")

    assert first.plan_id == "同一个名字"  # type: ignore[attr-defined]
    assert second.plan_id == "同一个名字-2"  # type: ignore[attr-defined]


def test_a_new_revision_keeps_the_original_name(service: PlanningService) -> None:
    """改标题不该让历史散成两份, 所以 id 只在新建时取一次."""
    first = _write(service, name="保持同名")

    second = _write(
        service,
        name="换了个完全不同的名字",
        title="改过的标题",
        plan_id=first.plan_id,  # type: ignore[attr-defined]
    )

    assert second.plan_id == "保持同名"  # type: ignore[attr-defined]
    assert second.revision == 2  # type: ignore[attr-defined]


def test_an_unusable_name_falls_back_instead_of_writing_a_weird_directory(
    service: PlanningService,
) -> None:
    """纯符号当目录名是危险的, 不是不好看."""
    written = _write(service, name="../../..")

    assert written.plan_id.startswith("plan-")  # type: ignore[attr-defined]
    assert "/" not in written.plan_id  # type: ignore[attr-defined]


def test_the_todo_keeps_its_name_while_being_corrected(
    service: PlanningService,
) -> None:
    """同名 = 继续修正同一份清单: revision 递增, 归档留得住上一版."""
    service.write_todo(["先读"], name="接线清单")

    second = service.write_todo(["先读", "再写"], name="接线清单")

    assert second.todo_id == "接线清单"
    assert second.revision == 2


def test_a_renamed_todo_starts_over(service: PlanningService) -> None:
    """换名 = 这是另一件事的清单, 从 revision 1 起算."""
    service.write_todo(["先读"], name="接线清单")

    second = service.write_todo(["另一件事"], name="回归清单")

    assert second.todo_id == "回归清单"
    assert second.revision == 1


def test_seeding_from_a_plan_reuses_the_plan_name(service: PlanningService) -> None:
    """同一件事在磁盘上就该是同一个名字."""
    plan = _write(service, name="播种命名")

    todo = service.seed_from_plan(plan)  # type: ignore[arg-type]

    assert todo.todo_id == "播种命名"
    assert todo.plan_id == "播种命名"
