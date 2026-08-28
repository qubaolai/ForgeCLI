"""构建入口命令必须能跑 (2026-08-28 回归).

真实现象: `npm run build 2>&1 | tail -20` 在 accept_edits 与 full_access 下都被判
`deny / script_content_unavailable`, 风险事实写着"脚本不存在或不是普通文件:
<cwd>/npm run build 2".

那个路径是 `" ".join((executable, *argv))` 拼出来的 —— 把整条命令行填进了
`ScriptPayload.path`, 而 path 的唯一消费者是"打开它, 读正文, 算哈希". 于是:

- 这个值在任何机器上都不可能是一个文件, 所以判定每次都失败, 不是偶发误判;
- 失败走的是 `cannot_run`, 也就是 `unrunnable`, 而 `unrunnable` 在 PolicyEngine 里
  排在模式判断**之前** —— 换模式, 点批准, 学习规则全都够不着它, `can_retry: false`;
- `_SCRIPT_ENTRYPOINTS` 有 19 个名字, 于是 npm / make / go / cargo / mvn / pytest /
  gradle 这一整类命令在所有模式下都不可执行.

后果不止是"少一个命令". 模型读到"脚本不存在", 推断成"这台机器没装 npm", 于是跳过构建
验证, 并把这个错误结论写进给用户的回答里 —— 那正是 e0ef227 想修的"模型永远验证不了
自己写的代码能不能起来", 只不过换了一个独立的成因.

判据落在 `ScriptPayload.bindable_path` 一处: 带空白的不是路径.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.security.authorization_service import ToolAuthorizationService
from forgecli.application.security.policy_engine import PolicyEngine
from forgecli.application.security.wiring import build_analyzer_registry
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
from forgecli.domain.security.shell.command_plan import ScriptPayload
from forgecli.domain.security.shell.wrappers import (
    _SCRIPT_ENTRYPOINTS,
    script_entry_of,
)
from forgecli.domain.security.vocabulary import Decision, DecisionReason
from forgecli.domain.tool.plan import ToolPlan
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from forgecli.shared.cancellation import CancelToken
from support.fakes import PROFILE, NullArtifactStore


class _NeverRuns(CommandExecutor):
    def run(
        self, request: CommandRequest, cancel: CancelToken | None = None
    ) -> CommandOutcome:
        raise AssertionError("裁决用例不应执行子进程")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir(parents=True)
    return root


@pytest.fixture
def toolchain(tmp_path: Path) -> Path:
    """一个真的能被受控 PATH 找到的 npm.

    不 mock 解析器: 这条 bug 的整条链路 (解析 -> 身份绑定 -> 脚本绑定 -> 裁决) 都要
    真跑一遍才算钉住.
    """
    bin_dir = tmp_path / "toolchain"
    bin_dir.mkdir()
    npm = bin_dir / "npm"
    npm.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    npm.chmod(0o755)
    return bin_dir


def _decide(
    workspace: Path,
    command: str,
    *,
    mode: SessionMode = SessionMode.ACCEPT_EDITS,
    extra_path: Path | None = None,
) -> AuthorizationDecision:
    path = "/usr/bin:/bin"
    if extra_path is not None:
        path = f"{extra_path}:{path}"
    tool = ShellRunTool(_NeverRuns(), ResourceGovernor(), NullArtifactStore())
    fence = fence_for(mode, workspace_roots=(str(workspace),))
    context = ExecutionContext(
        cwd=str(workspace),
        workspace_roots=(str(workspace),),
        environment={"PATH": path, "HOME": str(workspace.parent / "home")},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
        fence=fence,
    )
    plan = tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-1",
            tool_name="shell_run",
            arguments={"command": command, "shell_kind": "posix"},
            tool_call_id="c1",
        ),
        context,
    )
    assert isinstance(plan, ToolPlan), plan
    service = ToolAuthorizationService(
        build_analyzer_registry(ProtectedPathPolicy(roots=())), PolicyEngine()
    )
    return service.evaluate(
        plan,
        PolicyContext(
            mode=mode,
            session_id="s",
            turn_id="t",
            execution_profile_hash=PROFILE.execution_profile_hash,
            fence=fence,
            confined=True,
        ),
        context,
    )


# ---- 入口命令不声明脚本文件 ----


@pytest.mark.parametrize("name", sorted(_SCRIPT_ENTRYPOINTS))
def test_no_entrypoint_claims_a_script_file(name: str) -> None:
    """`npm run build` 跑的是 package.json 里那条 script, 不是一个叫 npm 的文件.

    这一整张表都没有"可以读进来算哈希的那一份正文", 所以一律 unresolved.
    """
    payload = script_entry_of(name, ("run", "build"))

    assert payload is not None
    assert payload.bindable_path is None
    assert payload.origin == "unresolved"


def test_the_reported_command_is_not_denied(workspace: Path, toolchain: Path) -> None:
    """用户报的那一条. 判据是"不是 DENY", 不是"一定 ALLOW" —— 临时目录里的 npm 是
    Agent 可写的, 按 SCRIPT_EXECUTION 走 ASK 属于另一条规则, 与本 bug 无关."""
    decision = _decide(workspace, "npm run build 2>&1 | tail -20", extra_path=toolchain)

    assert decision.decision is not Decision.DENY
    assert decision.reason is not DecisionReason.SCRIPT_CONTENT_UNAVAILABLE


@pytest.mark.parametrize(
    "mode", [SessionMode.ACCEPT_EDITS, SessionMode.FULL_ACCESS, SessionMode.AUTO]
)
def test_no_mode_could_rescue_it(
    workspace: Path, toolchain: Path, mode: SessionMode
) -> None:
    """`unrunnable` 排在模式判断之前, 所以这条 DENY 在 full_access 下同样落下来.

    这一条钉的是**为什么这个 bug 这么严重**: 它不是一次多余的确认, 是一条谁都够不着
    的拒绝.
    """
    decision = _decide(workspace, "npm run build", mode=mode, extra_path=toolchain)

    assert decision.decision is not Decision.DENY


def test_the_risk_facts_never_name_a_fabricated_path(
    workspace: Path, toolchain: Path
) -> None:
    """错误信息把模型带沟里了: 它读到"<cwd>/npm run build 2 不存在", 回答用户
    "系统 PATH 不含 npm", 然后跳过了构建验证."""
    decision = _decide(workspace, "npm run build 2>&1 | tail -20", extra_path=toolchain)

    for fact in decision.risk_facts:
        assert "npm run build" not in fact.detail


# ---- 判据本身 ----


def test_bindable_path_rejects_a_joined_command_line() -> None:
    payload = ScriptPayload(language="javascript", path="npm run build", origin="file")

    assert payload.path == "npm run build"
    assert payload.bindable_path is None


def test_bindable_path_keeps_a_real_token() -> None:
    payload = ScriptPayload(language="shell", path="./deploy.sh", origin="file")

    assert payload.bindable_path == "./deploy.sh"


# ---- 另一个方向: 真的指名了一个脚本文件, 而它不在 ----


def test_a_named_script_that_is_missing_is_still_denied(workspace: Path) -> None:
    """这条 DENY 是对的, 不能被上面的修复顺手抹掉: 路径来自命令自己, 这条命令跑起来
    也会失败."""
    decision = _decide(workspace, "bash ./deploy.sh")

    assert decision.decision is Decision.DENY
    assert decision.reason is DecisionReason.SCRIPT_CONTENT_UNAVAILABLE


def test_a_named_script_that_exists_still_binds(workspace: Path) -> None:
    (workspace / "deploy.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")

    decision = _decide(workspace, "bash ./deploy.sh")

    assert decision.decision is not Decision.DENY
