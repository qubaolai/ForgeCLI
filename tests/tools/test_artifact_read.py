"""artifact.read 与归档存储 (ADR-0032 决策 6 / 6.1 / 6.2).

这个工具是一级降级唯一的取回路径: 降级把 transcript 里的正文换成一行引用, 引用取不
回来的话, 压缩干的事就是"把内容删掉, 再告诉模型它在一个够不着的地方".

而它能存在的前提是 id 表达不了路径 —— 工具的 ToolPlan 不声明任何路径 target, 所以
workspace_analyzer 没有东西可判; 这不是绕过受保护路径那道 Hard Deny, 是根本不产生
受它管辖的路径. id 校验一旦缺失, 这个证明就塌了.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from forgecli.application.security.policy_engine import _allow_reason
from forgecli.application.tools.artifact_store import ArtifactMissing
from forgecli.application.tools.builtin.artifact_read import ArtifactReadTool
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.tool import ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.intents import SessionMode
from forgecli.domain.security.budget import capabilities_requiring_approval
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.vocabulary import DecisionReason
from forgecli.domain.tool.capability import (
    MUTATING_CAPABILITIES,
    Capability,
)
from forgecli.domain.tool.errors import PreparationError
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.tool.result import ToolResult, ToolResultStatus
from forgecli.infrastructure.tools.fs_artifact_store import FsArtifactStore
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from support.fakes import PROFILE


@pytest.fixture
def store(tmp_path: Path) -> FsArtifactStore:
    return FsArtifactStore(tmp_path / "artifacts")


def _context(tmp_path: Path) -> ExecutionContext:
    root = tmp_path / "ws"
    root.mkdir(exist_ok=True)
    return ExecutionContext(
        cwd=str(root),
        workspace_roots=(str(root),),
        environment={"PATH": "/usr/bin:/bin"},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )


def _run(
    store: FsArtifactStore, tmp_path: Path, **arguments: object
) -> ToolResult | PreparationError:
    tool = ArtifactReadTool(ResourceGovernor(), store)
    context = _context(tmp_path)
    prepared = tool.prepare(
        ToolInvocationRequest(
            tool_name="artifact.read",
            invocation_id="call-1",
            arguments=arguments,
        ),
        context,
    )
    if isinstance(prepared, PreparationError):
        return prepared
    assert isinstance(prepared, ToolPlan)
    return tool.perform(prepared, context)


# ---- 存储 ----


def test_an_id_that_walks_out_of_the_directory_is_refused(
    store: FsArtifactStore, tmp_path: Path
) -> None:
    """`_path_of` 拿 artifact_id[:2] 当目录名; ".." 会直接跳出 artifacts 目录.

    这条路径绕开的正是受保护路径那道 Hard Deny —— 工具内部走 ArtifactStore,
    不经 workspace_analyzer.
    """
    secret = tmp_path / "secret.txt"
    secret.write_text("绝密", "utf-8")

    with pytest.raises(ArtifactMissing):
        store.read("../../secret")

    assert store.exists("../../secret") is False


@pytest.mark.parametrize(
    "bad", ["", "短", "ZZZZZZZZZZZZZZZZ", "0123456789abcde", "0123456789abcdef0"]
)
def test_only_sixteen_lowercase_hex_characters_are_an_id(
    store: FsArtifactStore, bad: str
) -> None:
    assert store.exists(bad) is False


def test_a_window_reads_only_the_requested_lines(store: FsArtifactStore) -> None:
    ref = store.write(
        invocation_id="c1", name="output", data="".join(f"{n}\n" for n in range(1, 21))
    )

    assert store.read(ref.artifact_id, offset=3, limit=2) == "3\n4"


def test_a_sweep_removes_only_what_has_not_been_referenced_lately(
    store: FsArtifactStore,
) -> None:
    old = store.write(invocation_id="c1", name="output", data="旧的")
    fresh = store.write(invocation_id="c2", name="output", data="新的")
    stale = time.time() - 4 * 24 * 60 * 60
    os.utime(Path(old.path), (stale, stale))

    removed = store.sweep(older_than_seconds=3 * 24 * 60 * 60)

    assert removed == 1
    assert store.exists(old.artifact_id) is False
    assert store.exists(fresh.artifact_id) is True


def test_reading_renews_the_reference_clock(store: FsArtifactStore) -> None:
    """write 在内容已存在时跳过写入, 所以 mtime 不会自己刷新.

    touch 与 read 是唯一的续期来源.
    """
    ref = store.write(invocation_id="c1", name="output", data="内容")
    stale = time.time() - 4 * 24 * 60 * 60
    os.utime(Path(ref.path), (stale, stale))

    store.read(ref.artifact_id)

    assert store.sweep(older_than_seconds=3 * 24 * 60 * 60) == 0


# ---- 工具 ----


def test_it_reads_back_what_a_tool_archived(
    store: FsArtifactStore, tmp_path: Path
) -> None:
    ref = store.write(invocation_id="c1", name="output", data="归档的完整输出")

    result = _run(store, tmp_path, artifact_id=ref.artifact_id)

    assert isinstance(result, ToolResult)
    assert result.status is ToolResultStatus.OK
    assert result.text == "归档的完整输出"


def test_a_path_shaped_id_never_reaches_the_filesystem(
    store: FsArtifactStore, tmp_path: Path
) -> None:
    result = _run(store, tmp_path, artifact_id="../../../../etc/passwd")

    assert isinstance(result, PreparationError), "要在 prepare 就拦掉, 不是等到读文件"


def test_a_collected_artifact_says_it_was_collected_not_that_it_failed(
    store: FsArtifactStore, tmp_path: Path
) -> None:
    """说不清是清理机制还是 id 写错了, 模型就会一直重试同一个 id."""
    result = _run(store, tmp_path, artifact_id="0123456789abcdef")

    assert isinstance(result, ToolResult)
    assert result.error is not None
    assert result.error.code == "artifact_expired"
    assert result.error.retryable is False


def test_the_plan_declares_no_paths_at_all(
    store: FsArtifactStore, tmp_path: Path
) -> None:
    """ "目标集合在机制上封闭"就是这一条断言的内容 (照 plan.read 的形状)."""
    ref = store.write(invocation_id="c1", name="output", data="内容")
    tool = ArtifactReadTool(ResourceGovernor(), store)

    plan = tool.prepare(
        ToolInvocationRequest(
            tool_name="artifact.read",
            invocation_id="call-1",
            arguments={"artifact_id": ref.artifact_id},
        ),
        _context(tmp_path),
    )

    assert isinstance(plan, ToolPlan)
    assert plan.effects.read_paths == ()
    assert plan.effects.mutating_targets == ()


# ---- 能力 ----


def test_reading_an_archive_never_asks_the_user() -> None:
    """每次取回一段被降级掉的历史输出都要问一次人, 正是一级降级要省下的东西."""
    outside = capabilities_requiring_approval(
        frozenset({Capability.ARTIFACT_READ}), None, confined=False
    )

    assert outside == frozenset()


def test_it_stays_visible_in_plan_mode() -> None:
    assert Capability.ARTIFACT_READ not in MUTATING_CAPABILITIES


def test_a_lone_archive_read_is_audited_as_its_own_fast_path() -> None:
    """审计要答得出"这次为什么没问人", 而 RULE_ALLOW 会暗示有条规则匹配上了."""
    reason = _allow_reason(
        frozenset({Capability.ARTIFACT_READ}),
        PolicyContext(
            mode=SessionMode.ACCEPT_EDITS,
            session_id="s1",
            turn_id="t1",
            execution_profile_hash="h",
        ),
    )

    assert reason is DecisionReason.ARTIFACT_READ_FAST_PATH
