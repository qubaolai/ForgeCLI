"""记忆的注入通道与写入工具 (ADR-0033 决策 8 / 9)."""

from __future__ import annotations

import pytest

from forgecli.application.memory.memory_service import MemoryService
from forgecli.application.memory.memory_store import MemoryStore
from forgecli.application.prompt.runtime_facts import RuntimeFacts
from forgecli.application.prompt.system_prompt_builder import (
    PromptBuildInput,
    SystemPromptBuilder,
)
from forgecli.application.tools.builtin.memory_tools import (
    MemoryForgetTool,
    MemoryWriteTool,
)
from forgecli.application.tools.tool import ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.intents import SessionMode
from forgecli.domain.memory.entry import (
    MemoryEntry,
    MemoryProvenance,
    MemoryScope,
)
from forgecli.domain.prompt.blocks import PromptBlockId
from forgecli.domain.security.budget import capabilities_requiring_approval
from forgecli.domain.tool.capability import (
    CAPABILITY_VOCABULARY_VERSION,
    MUTATING_CAPABILITIES,
    Capability,
)
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.tool.result import ToolResult, ToolResultStatus
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from support.fakes import PROFILE, tool_plan


class _InMemoryStore(MemoryStore):
    def __init__(self) -> None:
        self.entries: tuple[MemoryEntry, ...] = ()

    def load(self) -> tuple[MemoryEntry, ...]:
        return self.entries

    def save(self, entries: tuple[MemoryEntry, ...]) -> None:
        self.entries = entries


def _entry(key: str, value: str, scope: MemoryScope) -> MemoryEntry:
    return MemoryEntry(
        key=key,
        value=value,
        scope=scope,
        provenance=MemoryProvenance(
            session_id="s", turn_id="t", created_at="2026-08-25T10:00:00+08:00"
        ),
    )


# ---- 决策 8: 注入通道 ----


def _facts() -> RuntimeFacts:
    return RuntimeFacts.from_profile(
        PROFILE,
        working_directory="/ws",
        workspace_roots=("/ws",),
        git_repository=False,
    )


def _build(memory: tuple[MemoryEntry, ...]):
    return SystemPromptBuilder().build(
        PromptBuildInput(mode=SessionMode.ACCEPT_EDITS, facts=_facts(), memory=memory)
    )


def test_no_memory_renders_no_block_at_all() -> None:
    """空的是常态 —— 新项目本来就没有记忆, 不该留一个写着"(无)"的空标题."""
    ids = [block.block_id for block in _build(()).blocks]

    assert PromptBlockId.MEMORY_STATE not in ids


def test_the_block_sits_after_the_cache_breakpoint() -> None:
    """模型可能在会话中途用 memory.write 改写它, 进稳定前缀就等于前缀不再稳定.

    PromptSnapshot 会在构造时自己校验这条划分, 所以这里断言的是它确实被标成易变的 ——
    构造成功只说明顺序对, 不说明标对了.
    """
    snapshot = _build((_entry("test_command", "make test", MemoryScope.PROJECT),))

    block = next(b for b in snapshot.blocks if b.block_id is PromptBlockId.MEMORY_STATE)
    assert block.cacheable is False
    assert snapshot.blocks[-1].block_id is PromptBlockId.MEMORY_STATE


def test_the_block_says_it_is_inferred_and_who_wins_a_conflict() -> None:
    """与项目指令的**信任级别不同**, 而模型分不出来 —— 除非我们说."""
    snapshot = _build((_entry("test_command", "make test", MemoryScope.PROJECT),))

    body = next(
        b.body for b in snapshot.blocks if b.block_id is PromptBlockId.MEMORY_STATE
    )
    assert "不是用户下达的指令" in body
    assert "项目指令" in body
    assert "make test" in body


def test_the_two_scopes_are_labelled_separately() -> None:
    snapshot = _build(
        (
            _entry("test_command", "make test", MemoryScope.PROJECT),
            _entry("language", "中文", MemoryScope.USER),
        )
    )

    body = next(
        b.body for b in snapshot.blocks if b.block_id is PromptBlockId.MEMORY_STATE
    )
    assert body.index("项目事实") < body.index("用户偏好")


# ---- 决策 9: 能力与工具 ----


def test_writing_memory_never_asks_the_user() -> None:
    """静默正是它的设计目标: 每次都要人点确认的记忆系统, 用户会在第三次时关掉它."""
    outside = capabilities_requiring_approval(
        frozenset({Capability.MEMORY_WRITE}), None, confined=False
    )

    assert outside == frozenset()


def test_memory_write_is_not_a_mutating_capability() -> None:
    """写的是 Forge 自己的状态目录, 碰不到工作区也碰不到工作区之外的用户文件."""
    assert Capability.MEMORY_WRITE not in MUTATING_CAPABILITIES


def test_adding_the_capability_did_not_invalidate_existing_authorizations() -> None:
    """增量扩词汇不该让用户为一件与他无关的事重新批准一遍 (ADR-0033 决策 9).

    这个哈希是**钉住**的. 它一变说明有人动了 plan_hash 的构成 —— 而 plan_hash 是
    always 学习规则的匹配键, 所以那会让用户已经批准过的每一条一次性失效, 全部重新问
    一遍. 那件事可以做, 但必须是有人明确决定要做, 不是加个枚举值顺手带出来的.
    """
    plan = tool_plan(
        tool_name="fs.read", capabilities=frozenset({Capability.WORKSPACE_READ})
    )

    assert CAPABILITY_VOCABULARY_VERSION == "1"
    assert plan.plan_hash == (
        "sha256:a8b9d304f1bf77d0b90b093f54f7db64b6855acc49f72c254bfd729f2df498fb"
    )


@pytest.fixture
def service() -> MemoryService:
    memory = MemoryService({scope: _InMemoryStore() for scope in MemoryScope})
    memory.begin_turn(session_id="s", turn_id="t")
    return memory


def _context(tmp_path) -> ExecutionContext:
    root = tmp_path / "ws"
    root.mkdir(exist_ok=True)
    return ExecutionContext(
        cwd=str(root),
        workspace_roots=(str(root),),
        environment={"PATH": "/usr/bin:/bin"},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )


def _run(tool, service: MemoryService, tmp_path, **arguments: object) -> ToolResult:
    context = _context(tmp_path)
    plan = tool.prepare(
        ToolInvocationRequest(
            tool_name=tool.spec.name, invocation_id="call-1", arguments=arguments
        ),
        context,
    )
    assert isinstance(plan, ToolPlan), plan
    return tool.perform(plan, context)


def test_the_write_tool_declares_no_paths(service: MemoryService, tmp_path) -> None:
    """写哪由 scope 与 project_id 算出, 模型指定不了."""
    tool = MemoryWriteTool(service)

    plan = tool.prepare(
        ToolInvocationRequest(
            tool_name="memory.write",
            invocation_id="call-1",
            arguments={"scope": "project", "key": "k", "value": "v"},
        ),
        _context(tmp_path),
    )

    assert isinstance(plan, ToolPlan)
    assert plan.effects.read_paths == ()
    assert plan.effects.mutating_targets == ()


def test_a_credential_write_comes_back_with_an_actionable_reason(
    service: MemoryService, tmp_path
) -> None:
    """一次莫名其妙的失败足以让一个工具从模型的选项里永久消失."""
    result = _run(
        MemoryWriteTool(service),
        service,
        tmp_path,
        scope="project",
        key="api",
        value="sk-proj-abc123def456",
    )

    assert result.status is ToolResultStatus.INVALID_INPUT
    assert result.error is not None
    assert result.error.code == "looks_like_secret"
    # 换个说法绕开凭证过滤不是我们希望它做的事.
    assert result.error.retryable is False
    assert "凭证" in result.text


def test_an_overwrite_tells_the_model_what_it_just_replaced(
    service: MemoryService, tmp_path
) -> None:
    """模型据此知道它刚推翻了自己以前的结论, 而不是新增了一条."""
    tool = MemoryWriteTool(service)
    _run(tool, service, tmp_path, scope="project", key="test_command", value="pytest")

    result = _run(
        tool, service, tmp_path, scope="project", key="test_command", value="make test"
    )

    assert "pytest" in result.text


def test_forgetting_something_never_recorded_is_not_an_error(
    service: MemoryService, tmp_path
) -> None:
    result = _run(
        MemoryForgetTool(service), service, tmp_path, scope="user", key="nothing"
    )

    assert result.status is ToolResultStatus.OK
    assert result.error is None
