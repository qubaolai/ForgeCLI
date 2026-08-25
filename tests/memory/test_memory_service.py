"""记忆的写入规则 (ADR-0033 决策 5 / 6 / 7).

静默写入把过滤的重要性抬高了一个量级: 没有人在看, 所以拒绝必须发生在写入侧, 而且
必须说清原因 —— 一次莫名其妙的失败足以让一个工具从模型的选项里永久消失 (ADR-0031
背景里记过同样的教训).
"""

from __future__ import annotations

import pytest

from forgecli.application.memory.memory_service import (
    MemoryRejection,
    MemoryService,
)
from forgecli.application.memory.memory_store import (
    MAX_ENTRIES_PER_SCOPE,
    MAX_VALUE_BYTES,
    MemoryStore,
)
from forgecli.domain.memory.entry import MemoryEntry, MemoryScope


class _InMemoryStore(MemoryStore):
    def __init__(self) -> None:
        self.entries: tuple[MemoryEntry, ...] = ()

    def load(self) -> tuple[MemoryEntry, ...]:
        return self.entries

    def save(self, entries: tuple[MemoryEntry, ...]) -> None:
        self.entries = entries


@pytest.fixture
def service() -> MemoryService:
    memory = MemoryService(
        {scope: _InMemoryStore() for scope in MemoryScope},
        clock=lambda: "2026-08-25T10:00:00+08:00",
    )
    memory.begin_turn(session_id="ses_01", turn_id="turn_0003")
    return memory


def test_a_remembered_fact_comes_back_with_where_it_came_from(
    service: MemoryService,
) -> None:
    """没有来源的坏记忆无法调试: "模型为什么突然认为测试命令是 X" 只有来源答得上来."""
    service.remember(
        MemoryScope.PROJECT, "test_command", "make test", derived_from="用户纠正过一次"
    )

    entry = service.load()[0]
    assert entry.value == "make test"
    assert entry.provenance.session_id == "ses_01"
    assert entry.provenance.turn_id == "turn_0003"
    assert entry.provenance.derived_from == "用户纠正过一次"


def test_writing_the_same_key_twice_replaces_instead_of_appending(
    service: MemoryService,
) -> None:
    """只增不改等于错误单调累积: 模型会看到两条互相矛盾的记忆并自己挑一条."""
    service.remember(MemoryScope.PROJECT, "test_command", "pytest")
    written = service.remember(MemoryScope.PROJECT, "test_command", "uv run pytest")

    assert written.accepted
    assert written.replaced == "pytest"
    assert [entry.value for entry in service.load()] == ["uv run pytest"]


@pytest.mark.parametrize(
    "value",
    [
        "sk-proj-abc123def456",
        "token 是 ghp_16C7e42F292c6912E7710c838347Ae178B4a",
        "-----BEGIN RSA PRIVATE KEY-----",
        "AKIAIOSFODNN7EXAMPLE",
        "xA9fQ2mZ8kLp3WvN7bTyR4dHs6JgE1cU",
    ],
)
def test_anything_shaped_like_a_credential_is_refused(
    service: MemoryService, value: str
) -> None:
    """静默写入意味着一条 API key 会被无声写进磁盘, 并在此后每一轮进提示词."""
    written = service.remember(MemoryScope.PROJECT, "api", value)

    assert written.rejection is MemoryRejection.LOOKS_LIKE_SECRET
    assert service.load() == ()


@pytest.mark.parametrize(
    "value",
    [
        "make test",
        "uv run pytest -q",
        "入口在 interfaces/cli/bootstrap.py",
        "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
    ],
)
def test_ordinary_project_facts_are_not_mistaken_for_credentials(
    service: MemoryService, value: str
) -> None:
    """误判的代价是模型再也不用这个工具, 所以纯十六进制长串刻意不拦."""
    assert service.remember(MemoryScope.PROJECT, "fact", value).accepted


def test_an_oversized_value_is_refused(service: MemoryService) -> None:
    """记忆每一轮都进上下文, 与 FORGE.md 那份一次性预算不是一回事."""
    written = service.remember(MemoryScope.PROJECT, "long", "x" * (MAX_VALUE_BYTES + 1))

    assert written.rejection is MemoryRejection.VALUE_TOO_LONG


def test_a_full_scope_refuses_instead_of_evicting_the_oldest(
    service: MemoryService,
) -> None:
    """淘汰要挑一个"最不重要"的, 而这里没有任何依据能挑对."""
    for index in range(MAX_ENTRIES_PER_SCOPE):
        assert service.remember(MemoryScope.PROJECT, f"k{index}", "v").accepted

    written = service.remember(MemoryScope.PROJECT, "one_more", "v")

    assert written.rejection is MemoryRejection.SCOPE_FULL


def test_a_full_scope_still_accepts_an_update_to_an_existing_key(
    service: MemoryService,
) -> None:
    """满了还不让改, 模型就没有任何办法修正一条错记忆 —— 只能眼看着它一直错下去."""
    for index in range(MAX_ENTRIES_PER_SCOPE):
        service.remember(MemoryScope.PROJECT, f"k{index}", "v")

    assert service.remember(MemoryScope.PROJECT, "k0", "改过了").accepted


def test_an_invalid_key_is_refused(service: MemoryService) -> None:
    assert (
        service.remember(MemoryScope.PROJECT, "有空格 的 key", "v").rejection
        is MemoryRejection.INVALID_KEY
    )


def test_forgetting_reports_whether_it_was_there(service: MemoryService) -> None:
    service.remember(MemoryScope.USER, "language", "中文")

    assert service.forget(MemoryScope.USER, "language") is True
    assert service.forget(MemoryScope.USER, "language") is False


def test_the_two_scopes_do_not_see_each_other(service: MemoryService) -> None:
    """项目事实跟项目走, 输出偏好跟人走 —— 所以它们不能同一个文件."""
    service.remember(MemoryScope.PROJECT, "same_key", "项目的")
    service.remember(MemoryScope.USER, "same_key", "用户的")

    assert [(e.scope, e.value) for e in service.load()] == [
        (MemoryScope.PROJECT, "项目的"),
        (MemoryScope.USER, "用户的"),
    ]


def test_the_merged_order_does_not_depend_on_write_history(
    service: MemoryService,
) -> None:
    """提示词必须是输入相同就字节相同的 (ADR-0018 §2.2)."""
    for key in ("zebra", "alpha", "middle"):
        service.remember(MemoryScope.PROJECT, key, "v")

    assert [entry.key for entry in service.load()] == ["alpha", "middle", "zebra"]
