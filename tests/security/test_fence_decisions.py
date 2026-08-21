"""围栏决定裁决, 不再由静态证明决定 (ADR-0030 决策 1 / 4).

替代了原先钉 ADR-0024 只读快速放行的那一组用例. 前提变了: 那条路径靠"分析证明这条命令
等价于一次读取"来免掉一次确认, 判据有八条, 每条都要维护; 现在靠的是**围栏让它伸不出去**,
判据只有一条 —— 要触达的东西在不在边界内.

差别最能说明问题的是 `rm -rf src/` 与 `find . -exec rm {} +`: 它们在旧模型下必然 ASK
(前者危险, 后者目标动态), 在围栏内则自动放行, 因为执行前已经打了快照, `/undo` 还得回来.

后一组用例更要紧: **没有围栏时必须落回 ASK**, 而不是退回去证明 (ADR-0030 决策 5).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.security.authorization_service import ToolAuthorizationService
from forgecli.application.security.policy_engine import PolicyEngine
from forgecli.application.security.wiring import build_analyzer_registry
from forgecli.application.tools.artifact_store import NullArtifactStore
from forgecli.application.tools.builtin.shell_run import ShellRunTool
from forgecli.application.tools.command_executor import (
    CommandExecutor,
    CommandOutcome,
    CommandRequest,
)
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.tool import ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.execution.fence import fence_for
from forgecli.domain.intents import SessionMode
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.decision import AuthorizationDecision
from forgecli.domain.security.protected_paths import ProtectedPathPolicy
from forgecli.domain.security.vocabulary import Decision, DecisionReason
from forgecli.domain.tool.plan import ToolPlan
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from forgecli.shared.cancellation import CancelToken
from support.fakes import PROFILE, unavailable_classifier


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
    mode: SessionMode = SessionMode.AUTO,
    confined: bool = True,
) -> AuthorizationDecision:
    tool = ShellRunTool(_NeverRuns(), ResourceGovernor(), NullArtifactStore())
    fence = fence_for(mode, workspace_roots=(str(workspace),))
    context = ExecutionContext(
        cwd=str(workspace),
        workspace_roots=(str(workspace),),
        environment={"PATH": "/usr/bin:/bin", "HOME": str(workspace.parent / "home")},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
        fence=fence,
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
            fence=fence,
            confined=confined,
        ),
        context,
    )


# ---- 围栏内: 自动放行 ----


@pytest.mark.parametrize(
    "command",
    [
        "ls -la src",
        "grep -rn foo src",
        "cat README.md | head -20",
        "find . -name '*.py'",
        "sort README.md",
        "xxd -l 16 README.md",
    ],
)
def test_reads_inside_the_fence_do_not_ask(workspace: Path, command: str) -> None:
    """曾经要靠一张 29 条的只读命令表才能放行, 现在不需要表."""
    decision = _decide(workspace, command)

    assert decision.decision is Decision.ALLOW
    assert decision.reason is DecisionReason.FENCE_CONFINED


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf src/",
        "sed -i 's/a/b/' src/a.py",
        "find . -name '*.tmp' -exec rm {} +",
        "printf '%s\\n' src/a.py | xargs rm",
        'eval "rm src/a.py"',
    ],
)
def test_workspace_mutations_inside_the_fence_do_not_ask(
    workspace: Path, command: str
) -> None:
    """写在围栏内就回滚得了, 所以不必先证明它写哪里.

    这一组在旧模型下全部 ASK: 前两条因为危险, 后三条因为目标由运行期产生 (DYNAMIC).
    围栏之后目标封不封闭只影响快照策略选 TARGETED 还是 FULL, 不影响裁决.
    """
    decision = _decide(workspace, command)

    assert decision.decision is Decision.ALLOW, decision.message


# ---- 无围栏: 落回 ASK, 不退回去证明 ----


@pytest.mark.parametrize("command", ["ls -la src", "rm -rf src/"])
def test_without_a_fence_everything_falls_back_to_ask(
    workspace: Path, command: str
) -> None:
    """ADR-0030 决策 5: 没有围栏就如实收紧, 不用静态分析补偿."""
    decision = _decide(workspace, command, confined=False)

    assert decision.decision is Decision.ASK


# ---- 模式边界 ----


def test_plan_mode_does_not_auto_allow_workspace_writes(workspace: Path) -> None:
    """plan 档的围栏没有可写工作区根.

    正常路径下模型根本看不见 shell.run (目录门, catalog_predicates.py), 这条钉的是
    裁决层不依赖目录过滤兜底.
    """
    decision = _decide(workspace, "rm -rf src/", mode=SessionMode.PLAN)

    assert decision.decision is Decision.ASK


def test_network_is_denied_outside_full_access(workspace: Path) -> None:
    decision = _decide(workspace, "curl -s https://example.com")

    assert decision.decision is Decision.ASK


def test_full_access_lets_network_through(workspace: Path) -> None:
    decision = _decide(
        workspace, "curl -s https://example.com", mode=SessionMode.FULL_ACCESS
    )

    assert decision.decision is Decision.ALLOW


def test_reading_outside_the_workspace_asks_even_in_full_access(
    workspace: Path,
) -> None:
    """full_access 的语义是放开网络, 不是放开宿主文件系统 (ADR-0030 决策 4)."""
    decision = _decide(workspace, "cat /etc/hosts", mode=SessionMode.FULL_ACCESS)

    assert decision.decision is Decision.ASK
