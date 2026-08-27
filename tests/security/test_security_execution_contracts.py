"""安全裁决与真实执行之间的绑定契约。"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from forgecli.application.security.analyzers.executable_binding import (
    _identity_chain,
)
from forgecli.application.security.analyzers.script_binding import (
    _MAX_BINDING_BYTES,
    ScriptBindingAnalyzer,
)
from forgecli.application.security.executable_resolver import ExecutableResolver
from forgecli.application.tools.registry import ToolRegistry
from forgecli.application.tools.runtime import ToolRuntime
from forgecli.application.tools.tool import Tool, ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.execution.environment import ShellLaunch
from forgecli.domain.execution.profile import ExecutionProfile
from forgecli.domain.intents import SessionMode
from forgecli.domain.security.approval import (
    ApprovalBinding,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalView,
)
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.executable_identity import TrustZone
from forgecli.domain.security.findings import AnalysisFindings
from forgecli.domain.security.shell.command_plan import ShellKind
from forgecli.domain.security.shell.parser import parse_command
from forgecli.domain.security.vocabulary import ApprovalScope
from forgecli.domain.tool.authorization import (
    AuthorizationError,
    ExecutionAuthorization,
    validate_narrowing,
)
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.errors import PreparationError
from forgecli.domain.tool.hashing import digest_bytes
from forgecli.domain.tool.plan import (
    FileStateBinding,
    PlanEffects,
    TargetResolution,
    ToolPlan,
    WorkspaceScope,
)
from forgecli.domain.tool.result import ContentPart, ToolResult, ToolResultStatus
from forgecli.domain.tool.spec import TargetDeclarationAbility, ToolSpec
from forgecli.infrastructure.execution.environment_probe import (
    build_execution_environment,
    probe_execution_profile,
)
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from forgecli.shared.cancellation import CancelToken
from support.fakes import tool_plan

_SPEC = ToolSpec(
    name="security_probe",
    version="1",
    title="安全契约替身",
    description="只用于运行时绑定测试",
    input_schema={"type": "object"},
    output_schema={"type": "object"},
    declared_capabilities=frozenset({Capability.WORKSPACE_READ}),
    target_declaration_ability=TargetDeclarationAbility.STATIC,
    default_timeout_seconds=5.0,
)


class _ProbeTool(Tool):
    performed = False

    @property
    def spec(self) -> ToolSpec:
        return _SPEC

    def prepare(
        self, request: ToolInvocationRequest, context: ExecutionContext
    ) -> ToolPlan | PreparationError:
        raise AssertionError("本测试不走 prepare")

    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        self.performed = True
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=plan.tool_name,
            status=ToolResultStatus.OK,
            content_parts=(ContentPart(text="performed"),),
        )


def _context(
    root: Path, *, environment: dict[str, str] | None = None
) -> ExecutionContext:
    profile = probe_execution_profile(protected_roots_hash="protected")
    filesystem = OsFileSystemView(version="security-contract")
    return ExecutionContext(
        cwd=str(root),
        workspace_roots=(str(root),),
        environment=environment or build_execution_environment(profile, raw={}),
        filesystem=filesystem,
        profile=profile,
    )


def _plan(context: ExecutionContext, *bindings: FileStateBinding) -> ToolPlan:
    return ToolPlan(
        plan_id="inv-security",
        tool_name=_SPEC.name,
        spec_hash=_SPEC.spec_hash,
        normalized_input=MappingProxyType({}),
        capabilities=frozenset({Capability.WORKSPACE_READ}),
        effects=PlanEffects(read_paths=(context.cwd,)),
        target_resolution=TargetResolution.STATIC,
        workspace_scope=WorkspaceScope.IN_WORKSPACE,
        execution_context=context.to_ref(),
        file_state_bindings=tuple(bindings),
    )


def _authorization(plan: ToolPlan, context: ExecutionContext) -> ExecutionAuthorization:
    now = time.time()
    return ExecutionAuthorization(
        authorization_id="auth-security",
        effective_plan=plan,
        execution_profile_hash=context.execution_profile_hash,
        issued_at_epoch=now,
        expires_at_epoch=now + 60,
    )


def _runtime(tool: _ProbeTool) -> ToolRuntime:
    registry = ToolRegistry()
    registry.register(tool)
    return ToolRuntime(registry)


def test_execution_context_copies_and_freezes_the_environment(tmp_path: Path) -> None:
    raw = {"PATH": "/usr/bin", "HOME": "/home/user"}
    context = _context(tmp_path, environment=raw)
    raw["PATH"] = "/tmp/attacker"

    assert context.environment["PATH"] == "/usr/bin"
    with pytest.raises(TypeError):
        context.environment["PATH"] = "/tmp/attacker"  # type: ignore[index]


def test_path_normalization_clamps_parent_segments_at_the_root(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    assert context.resolve("/../../etc//passwd") == "/etc/passwd"


def test_runtime_rejects_a_changed_execution_environment(tmp_path: Path) -> None:
    original = _context(tmp_path, environment={"PATH": "/usr/bin"})
    changed = ExecutionContext(
        cwd=original.cwd,
        workspace_roots=original.workspace_roots,
        environment={"PATH": "/bin", "HOME": "/different"},
        filesystem=original.filesystem,
        profile=original.profile,
    )
    plan = _plan(original)
    tool = _ProbeTool()

    with pytest.raises(AuthorizationError, match="environment_hash"):
        _runtime(tool).execute(_authorization(plan, original), changed)
    assert tool.performed is False


def test_runtime_rejects_a_script_or_executable_replaced_after_analysis(
    tmp_path: Path,
) -> None:
    target = tmp_path / "script.py"
    original = b"print('safe')\n"
    target.write_bytes(original)
    context = _context(tmp_path)
    facts = context.filesystem.facts(str(target))
    binding = FileStateBinding(
        path=str(target),
        realpath=facts.realpath,
        file_identity=facts.file_identity,
        size=facts.size,
        mtime_ns=facts.mtime_ns,
        content_hash=digest_bytes(original),
    )
    plan = _plan(context, binding)
    authorization = _authorization(plan, context)
    target.write_text("print('changed')\n", encoding="utf-8")
    tool = _ProbeTool()
    runtime = _runtime(tool)

    with pytest.raises(AuthorizationError, match="文件已变化"):
        runtime.execute(authorization, context)
    target.write_bytes(original)
    with pytest.raises(AuthorizationError, match="已被使用"):
        runtime.execute(authorization, context)
    assert tool.performed is False


def test_analyzer_cannot_remove_an_existing_file_state_binding(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    binding = FileStateBinding(
        path=str(tmp_path / "input"),
        realpath=str(tmp_path / "input"),
        file_identity="1:2",
        size=1,
        mtime_ns=1,
        content_hash=digest_bytes(b"x"),
    )
    original = _plan(context, binding)

    with pytest.raises(AuthorizationError, match="文件状态绑定"):
        validate_narrowing(original, replace(original, file_state_bindings=()))


def test_controlled_environment_overrides_host_injection_values() -> None:
    profile = probe_execution_profile(protected_roots_hash="protected")
    environment = build_execution_environment(
        profile,
        raw={
            "PATH": "/tmp/attacker",
            "PYTHONPATH": "/tmp/payload",
            "GIT_CONFIG_GLOBAL": "/tmp/evil.gitconfig",
            "HOME": "/home/user",
        },
    )

    assert "/tmp/attacker" not in environment["PATH"]
    assert "PYTHONPATH" not in environment
    assert environment["GIT_CONFIG_GLOBAL"] != "/tmp/evil.gitconfig"
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["PYTHONNOUSERSITE"] == "1"


def test_unknown_shell_kind_is_rejected_when_the_profile_is_built() -> None:
    profile = probe_execution_profile(protected_roots_hash="protected")
    with pytest.raises(ValueError, match="不支持的 Shell 方言"):
        replace(
            profile,
            shell_launch=ShellLaunch(
                program="/bin/custom", args=("-c",), kind="mystery"
            ),
        )


def test_explicit_toolchain_directory_is_not_treated_as_system(
    tmp_path: Path,
) -> None:
    toolchain = tmp_path / "toolchain" / "bin"
    toolchain.mkdir(parents=True)
    executable = toolchain / "runner"
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    profile = probe_execution_profile(
        protected_roots_hash="protected", toolchain_dirs=(str(toolchain),)
    )
    context = ExecutionContext(
        cwd=str(tmp_path),
        workspace_roots=(str(tmp_path / "workspace"),),
        environment=build_execution_environment(profile, raw={}),
        filesystem=OsFileSystemView(),
        profile=profile,
    )

    identity = ExecutableResolver().resolve("runner", context)
    assert identity.trust_zone is TrustZone.TOOLCHAIN
    assert identity.eligible_for_plain_allow is False


def _profile_with_trusted_bin(bin_dir: Path) -> ExecutionProfile:
    profile = probe_execution_profile(protected_roots_hash="protected")
    return replace(profile, trusted_path=(str(bin_dir),))


def test_a_symlink_in_the_controlled_path_stays_system(tmp_path: Path) -> None:
    """包管理器普遍在 bin 目录里放符号链接, 实体在版本化的另一棵目录树里.

    只按 realpath 判信任区的话, Homebrew 装的 python3 / node / npm 全部落进 UNKNOWN,
    每次调用都要人点头 —— 而 Nix, asdf, pyenv 的形状完全一样.
    """
    bin_dir = tmp_path / "brew" / "bin"
    bin_dir.mkdir(parents=True)
    cellar = tmp_path / "brew" / "Cellar" / "python@3.14" / "bin"
    cellar.mkdir(parents=True)
    (cellar / "python3.14").write_bytes(b"binary")
    (bin_dir / "python3").symlink_to(cellar / "python3.14")
    profile = _profile_with_trusted_bin(bin_dir)
    context = ExecutionContext(
        cwd=str(tmp_path),
        workspace_roots=(str(tmp_path / "workspace"),),
        environment=build_execution_environment(profile, raw={}),
        filesystem=OsFileSystemView(),
        profile=profile,
    )

    identity = ExecutableResolver().resolve("python3", context)

    assert identity.trust_zone is TrustZone.SYSTEM
    assert identity.eligible_for_plain_allow is True


def test_a_symlink_pointing_into_the_workspace_is_not_laundered(tmp_path: Path) -> None:
    """入口在受控 PATH 上不能给工作区里的文件洗白: 那是 Agent 自己写得了的内容."""
    bin_dir = tmp_path / "brew" / "bin"
    bin_dir.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "evil").write_bytes(b"binary")
    (bin_dir / "python3").symlink_to(workspace / "evil")
    profile = _profile_with_trusted_bin(bin_dir)
    context = ExecutionContext(
        cwd=str(tmp_path),
        workspace_roots=(str(workspace),),
        environment=build_execution_environment(profile, raw={}),
        filesystem=OsFileSystemView(),
        profile=profile,
    )

    identity = ExecutableResolver().resolve("python3", context)

    assert identity.trust_zone is TrustZone.WORKSPACE
    assert identity.eligible_for_plain_allow is False


def test_shebang_interpreter_is_part_of_the_executable_identity_chain(
    tmp_path: Path,
) -> None:
    interpreter = tmp_path / "interpreter"
    interpreter.write_bytes(b"binary")
    script = tmp_path / "task"
    script.write_text(f"#!{interpreter}\nprint('ok')\n", encoding="utf-8")
    context = _context(tmp_path)

    chain = _identity_chain(  # noqa: SLF001
        str(script), context, ExecutableResolver()
    )

    assert tuple(item.realpath for item in chain) == (
        str(script),
        str(interpreter),
    )


def _script_findings(path: Path, context: ExecutionContext) -> AnalysisFindings:
    command = parse_command(f"python3 {path.name}", ShellKind.POSIX, cwd=context.cwd)
    plan = ToolPlan(
        plan_id="inv-script",
        tool_name="shell_run",
        spec_hash="shell-spec",
        normalized_input=MappingProxyType({"command": command.raw_command}),
        capabilities=frozenset({Capability.EXECUTE_SHELL, Capability.EXECUTE_SCRIPT}),
        effects=PlanEffects(child_process=True, dynamic_execution=True),
        target_resolution=TargetResolution.UNKNOWN,
        workspace_scope=WorkspaceScope.IN_WORKSPACE,
        execution_context=context.to_ref(),
    )
    findings = AnalysisFindings(
        plan=plan,
        declared_capabilities=plan.capabilities,
        command_plan=command,
    )
    return ScriptBindingAnalyzer().analyze(
        findings,
        PolicyContext(
            mode=SessionMode.AUTO,
            session_id="session",
            turn_id="turn",
            execution_profile_hash=context.execution_profile_hash,
        ),
        context,
    )


def test_script_analysis_binds_the_exact_file_content(tmp_path: Path) -> None:
    script = tmp_path / "task.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    findings = _script_findings(script, _context(tmp_path))

    assert len(findings.plan.file_state_bindings) == 1
    assert findings.plan.file_state_bindings[0].path == str(script)


def test_a_script_too_large_to_hash_loses_its_binding_but_still_runs(
    tmp_path: Path,
) -> None:
    """行为随 ADR-0030 改变: 超限不再是拒绝, 而是"没有锚点"并如实记下来.

    旧行为是 DENY, 理由是"超过安全分析上限". 那条上限属于**分析器的实现限制**, 不是
    那条命令的性质 —— 一个 1MB 的脚本跑起来完全正常. 围栏之后不必分析它做什么, 只需要
    记住读到的是哪一份; 记不住就说记不住, 不该顺手把命令也拒掉.
    """
    script = tmp_path / "large.py"
    script.write_bytes(b"x" * (_MAX_BINDING_BYTES + 1))
    findings = _script_findings(script, _context(tmp_path))

    assert findings.unrunnable is None, "超限不该让命令变得不可执行"
    assert findings.plan.file_state_bindings == ()
    assert any(
        fact.code == "script_binding_unavailable" for fact in findings.risk_facts
    )


def _approval_request(*, scopes: tuple[ApprovalScope, ...]) -> ApprovalRequest:
    view = ApprovalView(
        plan=tool_plan(raw_command="make test"),
        action_summary="执行命令",
        allowed_scopes=scopes,
    )
    binding = ApprovalBinding(
        plan_hash=view.plan.plan_hash,
        catalog_snapshot_hash="catalog",
        execution_profile_hash="profile",
        policy_version="1",
        mode=SessionMode.ACCEPT_EDITS,
        view_hash=view.view_hash,
    )
    return ApprovalRequest(approval_id="approval-current", binding=binding, view=view)


def test_approval_response_must_match_the_current_request_id() -> None:
    request = _approval_request(scopes=(ApprovalScope.ONCE,))
    response = ApprovalResponse(
        outcome=ApprovalOutcome.APPROVED,
        approval_id="approval-other",
    )
    assert request.response_error(response) == "审批响应 id 与当前请求不一致"


def test_approval_response_cannot_expand_the_offered_scope() -> None:
    request = _approval_request(scopes=(ApprovalScope.ONCE,))
    response = ApprovalResponse(
        outcome=ApprovalOutcome.APPROVED,
        approval_id=request.approval_id,
        scope=ApprovalScope.WORKSPACE,
    )
    assert request.response_error(response) is not None
