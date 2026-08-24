"""能力推导的已修复丢失点.

共同形态是"事实被算出来了, 然后被静默丢掉". 这类缺陷不会报错, 只会让某一层再也看不到
它本该拦住的东西, 所以每条都要有断言钉住.

其中两条 (脚本正文里的网络访问进能力集, 以及它不得突破工具上界) 随 ADR-0030 一起删除:
从脚本正文推导能力这件事本身没有了 —— 脚本跑在围栏里, 它想联网会被内核拒绝, 不需要
先读正文猜它想不想.
"""

from __future__ import annotations

from forgecli.application.security.policy_engine import PolicyEngine
from forgecli.application.security.workspace_grants import GrantAccess, WorkspaceGrants
from forgecli.application.tools.builtin.shell_run import ShellRunTool
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.application.workspace.filesystem_view import (
    FileSystemView,
    PathFacts,
    PathKind,
)
from forgecli.domain.intents import SessionMode
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.findings import AnalysisFindings
from forgecli.domain.security.protected_paths import ProtectedPathPolicy
from forgecli.domain.security.vocabulary import Decision
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.plan import (
    ExecutionContextRef,
    PlanEffects,
    TargetResolution,
    ToolPlan,
    WorkspaceScope,
    empty_input,
)
from forgecli.infrastructure.execution.environment_probe import probe_execution_profile

_NARROWED = frozenset(
    {
        Capability.EXECUTE_SHELL,
        Capability.EXECUTE_SCRIPT,
        Capability.SPAWN_PROCESS,
    }
)


def _plan(capabilities: frozenset[Capability]) -> ToolPlan:
    return ToolPlan(
        plan_id="inv_1",
        tool_name="shell.run",
        spec_hash="spec",
        normalized_input=empty_input(),
        capabilities=capabilities,
        effects=PlanEffects(),
        target_resolution=TargetResolution.UNKNOWN,
        workspace_scope=WorkspaceScope.IN_WORKSPACE,
        execution_context=ExecutionContextRef(
            cwd="/w", environment_hash="env", filesystem_view_version="fs"
        ),
    )


def _declared() -> frozenset[Capability]:
    return ShellRunTool(None, None).spec.declared_capabilities  # type: ignore[arg-type]


def test_shell_run_declares_the_capabilities_its_analyzers_can_derive() -> None:
    """上界要真的是上界: 界外的事实即便被发现也无处安放, 会被交集抹掉."""
    declared = _declared()
    assert Capability.CREDENTIAL_ACCESS in declared
    assert Capability.WORKSPACE_READ in declared


def test_a_read_only_added_dir_is_not_writable() -> None:
    """`/add-dir` 不给写就是不给写. as_roots() 曾把 READ 与 WRITE 摊平成一件事."""
    grants = WorkspaceGrants(ProtectedPathPolicy(roots=()))
    grants.grant("/data/readable", GrantAccess.READ, granted_at="t0")
    grants.grant("/data/writable", GrantAccess.WRITE, granted_at="t0")

    # 两者都可读, 所以都是 root.
    assert grants.as_roots() == ("/data/readable", "/data/writable")
    # 只有前者进只读集合.
    assert grants.readonly_roots() == ("/data/readable",)

    # 断言实际接线的那一条路: 只读授权目录的**写入**按区外算, 于是要 EXTERNAL_WRITE
    # 而不是 WORKSPACE_WRITE, 而 EXTERNAL_WRITE 不在 accept_edits 与 auto 的预算里.
    context = ExecutionContext(
        cwd="/ws",
        workspace_roots=("/ws", *grants.as_roots()),
        environment={},
        filesystem=_LiteralFileSystem(),
        profile=probe_execution_profile(protected_roots_hash="h"),
        readonly_roots=grants.readonly_roots(),
    )
    assert context.scope_of("/data/readable/x") is WorkspaceScope.ADDED_DIR
    assert context.scope_for_write("/data/readable/x") is WorkspaceScope.OUTSIDE
    assert context.scope_for_write("/data/writable/x") is WorkspaceScope.ADDED_DIR


def test_never_auto_capabilities_ask_mandatorily() -> None:
    """CREDENTIAL_ACCESS / UNKNOWN 这类能力必须是 Mandatory Ask.

    只落普通 ASK 的话, 一条学习规则就能把它抬成 ALLOW —— "读凭证永远需要人类在场"
    和 ADR-0013 §4.1 的逐次批准都会在用户点过一次 always 之后失效.
    """
    for capability in (Capability.CREDENTIAL_ACCESS, Capability.UNKNOWN):
        plan = _plan(_NARROWED | {capability})
        findings = AnalysisFindings(plan=plan, declared_capabilities=plan.capabilities)
        decision = PolicyEngine().decide(
            findings,
            PolicyContext(
                mode=SessionMode.FULL_ACCESS,
                session_id="s1",
                turn_id="t1",
                execution_profile_hash="profile",
            ),
        )
        assert decision.decision is Decision.ASK
        assert decision.mandatory is True, capability


class _LiteralFileSystem(FileSystemView):
    """只解析字面路径的视图: 这条测试要验的是授权分级, 不是符号链接解析."""

    @property
    def version(self) -> str:
        return "literal"

    def facts(self, path: str) -> PathFacts:
        return PathFacts(path=path, realpath=path, kind=PathKind.FILE)

    def read_text(self, path: str, *, max_bytes: int) -> str:
        return ""

    def read_bytes(self, path: str, *, max_bytes: int) -> bytes:
        return b""

    def list_dir(self, path: str) -> tuple[str, ...]:
        return ()

    def expand_glob(
        self, pattern: str, *, root: str, max_results: int | None = None
    ) -> tuple[str, ...]:
        return ()
