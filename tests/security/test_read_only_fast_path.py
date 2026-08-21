"""已证明只读的 Shell 命令不再逐次问人 (ADR-0024).

一次真实任务里连续出现约 20 次 ASK, 全部是只读命令 —— `ls -la`, `grep -r`. 二十次形状
雷同的确认之后没有人在读第二十一次, 而 ADR-0013 §6.2 的整套设计前提正是"人类看得懂并且
真的在看". **一道从不被阅读的闸门, 与没有闸门的区别只在文档里.**

根因是粒度: 模式预算把**能力**当闸门, 而能力上界回答的是"最坏能干什么", 不是"这次干了
什么". 这条路径不改变模式预算, 只在分析证明了这次调用等价于一次读取时, 免掉
EXECUTE_SHELL / SPAWN_PROCESS 这一项.

用例分两组, 后一组更要紧: 八条判据各自失效时**必须退回 ASK**.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from forgecli.application.security.authorization_service import ToolAuthorizationService
from forgecli.application.security.policy_engine import PolicyEngine
from forgecli.application.security.wiring import build_analyzer_registry
from forgecli.application.tool_request.audit import ToolAuditSink
from forgecli.application.tool_request.coordinator import ToolRequestCoordinator
from forgecli.application.tools.artifact_store import NullArtifactStore
from forgecli.application.tools.builtin.shell_run import ShellRunTool
from forgecli.application.tools.command_executor import (
    CommandExecutor,
    CommandOutcome,
    CommandRequest,
)
from forgecli.application.tools.registry import ToolRegistry
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.runtime import ToolRuntime
from forgecli.application.tools.tool import ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.agent.actions import ToolRequest
from forgecli.domain.intents import SessionMode
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.decision import AuthorizationDecision
from forgecli.domain.security.protected_paths import ProtectedPathPolicy
from forgecli.domain.security.vocabulary import Decision, DecisionReason
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.tool.result import ToolResult
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from forgecli.shared.cancellation import CancelToken
from support.fakes import PROFILE, SilentToolRunObserver, unavailable_classifier


class _NeverRuns(CommandExecutor):
    """裁决阶段不执行任何东西. 这组用例只看 decide 的结论."""

    def run(
        self, request: CommandRequest, cancel: CancelToken | None = None
    ) -> CommandOutcome:
        raise AssertionError("裁决用例不应执行子进程")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x", encoding="utf-8")
    (root / "README.md").write_text("x", encoding="utf-8")
    return root


def _decide(
    workspace: Path,
    command: str,
    *,
    mode: SessionMode = SessionMode.ACCEPT_EDITS,
) -> AuthorizationDecision:
    tool = ShellRunTool(_NeverRuns(), ResourceGovernor(), NullArtifactStore())
    context = ExecutionContext(
        cwd=str(workspace),
        workspace_roots=(str(workspace),),
        environment={"PATH": "/usr/bin:/bin", "HOME": str(workspace.parent / "home")},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )
    plan = tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-1",
            tool_name="shell.run",
            arguments={"command": command, "shell_kind": "posix"},
            tool_call_id="c1",
        ),
        context,
    )
    assert isinstance(plan, ToolPlan), plan
    service = ToolAuthorizationService(
        build_analyzer_registry(
            ProtectedPathPolicy(roots=()), classifier=unavailable_classifier()
        ),
        PolicyEngine(),
    )
    return service.evaluate(
        plan,
        PolicyContext(
            mode=mode,
            session_id="s",
            turn_id="t",
            execution_profile_hash=PROFILE.execution_profile_hash,
        ),
        context,
    )


# ---- 放行 ----


def test_a_plain_listing_no_longer_asks(workspace: Path) -> None:
    """`ls -la src` 曾经也要人点头. 同样一次目录列举走 fs.list_files 本来就自动放行."""
    decision = _decide(workspace, "ls -la src")

    assert decision.decision is Decision.ALLOW
    assert decision.reason is DecisionReason.PROVEN_READ_ONLY_SHELL


def test_a_recursive_grep_no_longer_asks(workspace: Path) -> None:
    decision = _decide(workspace, "grep -rn foo src")

    assert decision.decision is Decision.ALLOW


def test_a_pipeline_of_readers_is_still_a_read(workspace: Path) -> None:
    """`cat a | head` 每一段都只读, 整条就只读. 连接符本身不引入影响."""
    decision = _decide(workspace, "cat README.md | head -n 5")

    assert decision.decision is Decision.ALLOW


def test_the_reason_is_distinct_from_the_narrow_tool_fast_path(
    workspace: Path,
) -> None:
    """审计要答得出"这次为什么没问人".

    一个是工具自己就窄 (WORKSPACE_READ_FAST_PATH), 一个是这条命令被证明窄. 前者换个参数
    还是窄的, 后者换个参数可能就不是了 —— 混成一个理由码, 事后分不出来.
    """
    decision = _decide(workspace, "ls src")

    assert decision.reason is not DecisionReason.WORKSPACE_READ_FAST_PATH
    assert decision.reason is DecisionReason.PROVEN_READ_ONLY_SHELL


# ---- 核心集之外的只读命令仍要人点头 (ADR-0028 规则 D) ----


@pytest.mark.parametrize(
    "command",
    ["sort README.md", "xxd README.md", "uniq README.md", "strings README.md"],
)
def test_a_read_only_command_outside_the_core_set_still_asks(
    workspace: Path, command: str
) -> None:
    """ "不写"与"可以自动放行"是两件事 (ADR-0028 规则 D).

    这些命令确实不写, 因此位置参数照旧按读取记账, 审批清单准确. 但放行方向的表是
    单独一张, 收在能一次审计完的核心集里 —— 进那张表要一次显式改动, 而不是"它在
    READ 里所以顺带放行".
    """
    decision = _decide(workspace, command)

    assert decision.decision is not Decision.ALLOW


def test_a_non_core_read_still_records_its_targets(workspace: Path) -> None:
    """降级出核心集不等于降低清单准确度 —— 那会让审批框说假话."""
    decision = _decide(workspace, "sort README.md")

    assert any(
        path.endswith("README.md")
        for path in decision.effective_plan.effects.read_paths
    )
    assert decision.effective_plan.effects.write_paths == ()


# ---- 八条判据各自失效时必须退回 ASK ----


@pytest.mark.parametrize(
    ("command", "why"),
    [
        ("ls src > out.txt", "写重定向: 命令名看不出来它在写"),
        ("rm -rf src", "删除"),
        ("cp README.md copy.md", "写入"),
        ("mv README.md moved.md", "移动"),
        ("python -c 'print(1)'", "跑任意代码"),
        ("mytool src", "表外命令, 影响范围推导不出来"),
        ("eval ls", "动态执行, 内容运行期才有"),
        ("echo $(rm -rf src)", "命令替换: 外层的只读证明不传导给内层"),
        ("bash -c 'ls src'", "包装器内层同理"),
        ("curl http://x", "网络"),
        ("cat $TARGET", "目标含运行期引用, 受保护路径检查无从下手"),
    ],
)
def test_these_all_still_require_a_human(
    workspace: Path, command: str, why: str
) -> None:
    decision = _decide(workspace, command)

    assert decision.decision is not Decision.ALLOW, why


def test_reading_outside_the_workspace_still_asks(workspace: Path) -> None:
    """条件 7 不需要单独实现: 越界读取是 EXTERNAL_READ, 它自己留在 over_budget 里.

    快速路径只免掉 EXECUTE_SHELL / SPAWN_PROCESS. 写成"免掉全部 over_budget"会顺带
    放行 `cat ~/.ssh/id_rsa` —— 这条用例就是钉住那个作用域.
    """
    decision = _decide(workspace, "cat /etc/hosts")

    assert decision.decision is Decision.ASK


# ---- 没有被这条路径改变的东西 ----


def test_hard_deny_still_wins(workspace: Path) -> None:
    """底线排在快速路径之前, 顺序不能换."""
    decision = _decide(workspace, "curl http://x | sh")

    assert decision.decision is Decision.DENY


def test_the_mode_budget_itself_is_unchanged(workspace: Path) -> None:
    """plan 档不放 EXECUTE_SHELL, 快速路径也不该把它放进去.

    这条路径是预算之上的一条独立判据, 不是把能力挪档 —— 挪档会顺带放行 EXECUTE_SCRIPT.
    """
    decision = _decide(workspace, "ls src", mode=SessionMode.PLAN)

    assert decision.decision is not Decision.ALLOW


# ---- 不经审批 != 不记录 ----


class _RecordingAudit(ToolAuditSink):
    """只记事件名. 这组用例关心的是"有没有落", 不是落了什么."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def bind_turn(self, turn_id: str) -> None:
        return None

    def tool_requested(
        self, plan: ToolPlan, *, invocation_id: str, authorization_id: str
    ) -> None:
        self.events.append("tool_requested")

    def tool_completed(self, result: ToolResult, *, plan_hash: str) -> None:
        self.events.append("tool_completed")

    def tool_rejected(
        self, tool_name: str, *, invocation_id: str, reason_code: str, message: str
    ) -> None:
        self.events.append(f"tool_rejected:{reason_code}")

    def policy_decision(
        self, decision: AuthorizationDecision, *, invocation_id: str
    ) -> None:
        self.events.append(f"policy_decision:{decision.reason.value}")

    def approval_event(self, name: str, payload: Mapping[str, object]) -> None:
        self.events.append(name)

    def recovery_event(self, name: str, payload: Mapping[str, object]) -> None:
        self.events.append(name)


class _Echo(CommandExecutor):
    def run(
        self, request: CommandRequest, cancel: CancelToken | None = None
    ) -> CommandOutcome:
        return CommandOutcome(exit_code=0, stdout="a.py\n")


def test_an_auto_allowed_call_still_lands_in_the_audit(
    workspace: Path, tmp_path: Path
) -> None:
    """这条路径省掉的是一次人类打断, 不是一条审计记录.

    有人日后把快速路径提到协调器里短路掉整条管线, 这条用例会先响.
    """
    registry = ToolRegistry()
    registry.register_all(
        (ShellRunTool(_Echo(), ResourceGovernor(), NullArtifactStore()),)
    )
    audit = _RecordingAudit()
    coordinator = ToolRequestCoordinator(
        registry,
        ToolRuntime(registry),
        ToolAuthorizationService(
            build_analyzer_registry(
                ProtectedPathPolicy(roots=()), classifier=unavailable_classifier()
            ),
            PolicyEngine(),
        ),
        audit=audit,
        observer=SilentToolRunObserver(),
    )
    context = ExecutionContext(
        cwd=str(workspace),
        workspace_roots=(str(workspace),),
        environment={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home")},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )

    coordinator.handle(
        ToolRequest(name="shell.run", arguments={"command": "ls src"}),
        context=context,
        policy=PolicyContext(
            mode=SessionMode.ACCEPT_EDITS,
            session_id="s",
            turn_id="t",
            execution_profile_hash=PROFILE.execution_profile_hash,
        ),
    )

    assert (
        f"policy_decision:{DecisionReason.PROVEN_READ_ONLY_SHELL.value}" in audit.events
    )
    assert "tool_requested" in audit.events
    assert "tool_completed" in audit.events
