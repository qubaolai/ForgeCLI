"""跨用例共享的测试替身.

只放"每个用例都要用一份, 但内容与被测行为无关"的东西 (执行画像, 假分类器). 与被测
行为相关的替身留在各自用例里 —— 藏进共享 fake 的断言前提最容易在改动后悄悄失真.
"""

from __future__ import annotations

from forgecli.application.manual_shell.provider import ManualShellObserver
from forgecli.application.prompt.project_instruction_reader import (
    ProjectInstruction,
    ProjectInstructionReader,
)
from forgecli.application.prompt.runtime_facts import RuntimeFacts
from forgecli.application.prompt.system_prompt_builder import (
    PromptBuildInput,
    SystemPromptBuilder,
    ToolBrief,
)
from forgecli.application.security.executable_resolver import (
    EXECUTABLE_RESOLUTION_VERSION,
)
from forgecli.application.tool_request.run_observer import ToolRunObserver
from forgecli.application.tools.artifact_store import ArtifactRef, ArtifactStore
from forgecli.domain.execution.environment import (
    DEFAULT_ENV_ALLOWLIST,
    ShellLaunch,
)
from forgecli.domain.execution.profile import ExecutionProfile, IsolationLevel
from forgecli.domain.intents import SessionMode
from forgecli.domain.manual_shell.request import ManualShellRequest
from forgecli.domain.manual_shell.result import ManualShellResult
from forgecli.domain.prompt.blocks import PromptSnapshot
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.hashing import digest_text
from forgecli.domain.tool.plan import (
    ExecutionContextRef,
    PlanEffects,
    ShellSubject,
    TargetResolution,
    ToolPlan,
    WorkspaceScope,
)

__all__ = ["PROFILE", "tool_plan"]

# 一份确定性执行画像: 测试默认无围栏 (ADR-0030 决策 3).
PROFILE = ExecutionProfile(
    platform="posix",
    isolation_level=IsolationLevel.UNCONFINED,
    trusted_path=("/usr/bin", "/bin"),
    shell_launch=ShellLaunch(program="/bin/sh", args=("-c",), kind="posix"),
    environment_allowlist=DEFAULT_ENV_ALLOWLIST,
    protected_roots_hash="test-protected-roots",
    executable_resolution_version=EXECUTABLE_RESOLUTION_VERSION,
    path_separator=":",
)

FACTS = RuntimeFacts.from_profile(
    PROFILE,
    working_directory="/ws",
    workspace_roots=("/ws",),
    git_repository=False,
)


class NoProjectInstructions(ProjectInstructionReader):
    """不读任何项目指令.

    只在测试里存在. 生产的组合根总是装 FsProjectInstructionReader —— 给
    AgentTurnService 留一个"默认不读"是为了让测试少传一个参数, 那属于为测试写生产代码.
    """

    def read(self, workspace_roots: tuple[str, ...]) -> tuple[ProjectInstruction, ...]:
        return ()


def prompt(
    mode: SessionMode = SessionMode.ACCEPT_EDITS,
    *,
    tools: tuple[ToolBrief, ...] = (),
    instructions: tuple[ProjectInstruction, ...] = (),
) -> PromptSnapshot:
    """给测试用的真提示词快照.

    走真的 SystemPromptBuilder 而不是手搓一个假 PromptSnapshot: 那样每次改内置文本都要
    同步改这里, 而它们本来就该一起变. 用真 builder 的另一个好处是, 任何让 builder 造不
    出合法快照的改动都会在整个测试套件里立刻显形.
    """
    return SystemPromptBuilder().build(
        PromptBuildInput(
            mode=mode,
            facts=FACTS,
            available_tools=tools,
            project_instructions=instructions,
        )
    )


class SilentToolRunObserver(ToolRunObserver):
    """什么都不发的运行观察者.

    只在测试里存在. 生产的组合根总是装 EventBusToolRunObserver —— 给协调器留一个
    "默认不观察"会让"忘了接终端"变成一个没人发现的静默降级.
    """

    def bind_turn(self, turn_id: str) -> None:
        return None

    def tool_prepared(self, *args: object, **kwargs: object) -> None:
        return None

    def policy_resolved(self, *args: object, **kwargs: object) -> None:
        return None

    def approval_requested(self, *args: object, **kwargs: object) -> None:
        return None

    def approval_resolved(self, *args: object, **kwargs: object) -> None:
        return None

    def tool_started(self, *args: object, **kwargs: object) -> None:
        return None

    def tool_completed(self, *args: object, **kwargs: object) -> None:
        return None

    def tool_cancelled(self, *args: object, **kwargs: object) -> None:
        return None

    def tool_rejected(self, *args: object, **kwargs: object) -> None:
        return None


def tool_plan(
    *,
    tool_name: str = "shell.run",
    raw_command: str | None = "poetry run pytest tests/agent_loop -q",
    capabilities: frozenset[Capability] = frozenset({Capability.EXECUTE_SHELL}),
    target_resolution: TargetResolution = TargetResolution.FORGE_EXPANDED,
    workspace_scope: WorkspaceScope = WorkspaceScope.IN_WORKSPACE,
    **effects: object,
) -> ToolPlan:
    """一份最小可用的 ToolPlan.

    审批视图与绑定的用例都要一份 plan 才能构造 —— ADR-0028 之后路径, 目标封闭度与
    写入内容全部从 plan 现读, 不再由视图自己存. 只放"每个用例都要用一份, 但内容与
    被测行为无关"的默认值; 与断言相关的字段由用例显式传进来.
    """
    return ToolPlan(
        plan_id="plan-1",
        tool_name=tool_name,
        spec_hash="sha256:spec",
        normalized_input={"command": raw_command or ""},
        capabilities=capabilities,
        effects=PlanEffects(**effects),  # type: ignore[arg-type]
        target_resolution=target_resolution,
        workspace_scope=workspace_scope,
        execution_context=ExecutionContextRef(
            cwd="/workspace/forge", environment_hash="e"
        ),
        analysis_subject=ShellSubject(raw_command=raw_command) if raw_command else None,
    )


class NullArtifactStore(ArtifactStore):
    """不落盘的产物库: 内容直接丢弃, 只保留大小与哈希.

    **只在测试里存在.** 它原先住在 `application/tools/artifact_store.py`, docstring 里
    写着两个用途: 纯内存测试, 以及"用户明确不想留产物". 后一个从来没有接线 —— 组合根
    永远装的是 FsArtifactStore, 而所有工具的 artifacts 参数本来就接受 None.
    一个只有测试构造的类不该住在生产代码里 (ADR-0028 规则 C).

    它仍然如实报告 truncated, 不会假装输出完整: 丢内容可以, 骗调用方不行.
    """

    def write(self, *, invocation_id: str, name: str, data: str) -> ArtifactRef:
        return ArtifactRef(
            artifact_id=f"{invocation_id}:{name}",
            path="",
            size=len(data.encode("utf-8")),
            content_hash=digest_text(data),
            truncated=True,
        )

    def read(self, artifact_id: str) -> str:
        raise KeyError(f"NullArtifactStore 不保存内容: {artifact_id}")


class NullManualShellObserver(ManualShellObserver):
    """什么都不打的人工 Shell 观察者.

    **只在测试里存在.** ManualShellService 把 observer 声明成**必填**, 理由写在它的
    构造函数注释里: 给它默认值就意味着漏接 observer 的组合根不会报错. 生产代码里再
    放一个空实现, 等于把那条理由绕开了.
    """

    def entered(self, request: ManualShellRequest) -> None:
        return None

    def exited(self, result: ManualShellResult) -> None:
        return None
