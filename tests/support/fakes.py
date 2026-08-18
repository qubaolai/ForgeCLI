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
from forgecli.application.security.classifier import (
    ClassifierRequest,
    FailSafeClassifier,
    LlmSafetyClassifier,
)
from forgecli.application.security.executable_resolver import (
    EXECUTABLE_RESOLUTION_VERSION,
)
from forgecli.domain.agent.prompt import PromptSnapshot
from forgecli.domain.execution.environment import (
    DEFAULT_ENV_ALLOWLIST,
    ShellLaunch,
)
from forgecli.domain.execution.profile import ExecutionProfile, IsolationLevel
from forgecli.domain.intents import SessionMode
from forgecli.domain.security.risk import RiskLevel, RiskReport

__all__ = ["PROFILE"]

# 一份确定性执行画像: 沙箱层未实现, 只有 NO_SANDBOX 一档 (ADR-0014 §4.1).
PROFILE = ExecutionProfile(
    platform="posix",
    isolation_level=IsolationLevel.NO_SANDBOX,
    trusted_path=("/usr/bin", "/bin"),
    shell_launch=ShellLaunch(program="/bin/sh", args=("-c",), kind="posix"),
    environment_allowlist=DEFAULT_ENV_ALLOWLIST,
    protected_roots_hash="test-protected-roots",
    executable_resolution_version=EXECUTABLE_RESOLUTION_VERSION,
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


class StubSafetyClassifier(LlmSafetyClassifier):
    """不联网的分类器替身. 结论由构造参数决定, 确定性输出.

    只在测试里存在. 生产代码里放一个默认返回 LOW / allow 的 fake, 一旦被误装进组合根
    就是整条链路上最隐蔽的一处 fail-open —— "没接分类器"会变成"分类器说没问题".
    """

    def __init__(
        self, report: RiskReport | None = None, *, raises: Exception | None = None
    ) -> None:
        # 不调父类 __init__: 它要一个真的 LlmGateway, 而这里根本不发请求.
        self._report = report
        self._raises = raises
        self.calls: list[ClassifierRequest] = []

    def classify(self, request: ClassifierRequest) -> RiskReport:
        self.calls.append(request)
        if self._raises is not None:
            raise self._raises
        return self._report or RiskReport(
            risk_level=RiskLevel.LOW,
            confidence=0.95,
            intent_aligned=True,
            summary="stub: 未发现风险",
            recommendation="allow",
        )


def unavailable_classifier() -> FailSafeClassifier:
    """给不关心分类器的用例用: 任何调用都归"不可用", 也就是 ASK.

    方向与生产一致 —— 拿不到结论时落 ASK, 而不是放行.
    """
    return FailSafeClassifier(StubSafetyClassifier(raises=TimeoutError("stub")))
