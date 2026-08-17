"""跨用例共享的测试替身.

只放"每个用例都要用一份, 但内容与被测行为无关"的东西 (执行画像, 假分类器). 与被测
行为相关的替身留在各自用例里 —— 藏进共享 fake 的断言前提最容易在改动后悄悄失真.
"""

from __future__ import annotations

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
from forgecli.domain.agent.prompt import PromptSnapshot
from forgecli.domain.execution.environment import (
    DEFAULT_ENV_ALLOWLIST,
    ShellLaunch,
)
from forgecli.domain.execution.profile import ExecutionProfile, IsolationLevel
from forgecli.domain.intents import SessionMode

__all__ = ["PROFILE"]

# 一份确定性执行画像: 沙箱层未实现, 只有 NO_SANDBOX 一档 (ADR-0014 §4.1).
PROFILE = ExecutionProfile(
    platform="posix",
    isolation_level=IsolationLevel.NO_SANDBOX,
    trusted_path=("/usr/bin", "/bin"),
    shell_launch=ShellLaunch(program="/bin/sh", args=("-c",), kind="posix"),
    environment_allowlist=DEFAULT_ENV_ALLOWLIST,
    protected_roots_hash="test-protected-roots",
)

FACTS = RuntimeFacts.from_profile(
    PROFILE, working_directory="/ws", workspace_roots=("/ws",)
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
