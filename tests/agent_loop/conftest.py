"""驱动一轮循环的小驱动器.

模仿 AgentTurnService._run_loop, 但一行执行工具的代码都没有.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

from forgecli.application.agent_loop.builtin_loop import BuiltinAgentLoop
from forgecli.application.agent_loop.rules import builtin_rules
from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.application.context.window_manager import WindowManager
from forgecli.application.llm.transport_policy import ModelTransportPolicy
from forgecli.domain.agent.actions import (
    AnswerAction,
    LoopAction,
    LoopObservation,
    LoopStop,
    ObservationSource,
    ToolRequestAction,
)
from forgecli.domain.agent.state import LoopInput
from support.loop_fakes import (
    FakeMeter,
    FakeSnapshotProvider,
    RecordingSubscriber,
    ScriptedGateway,
    Step,
    recording_bus,
)

ToolAnswer = Callable[[ToolRequestAction], LoopObservation]


def tool_ok(text: str = "ok") -> ToolAnswer:
    return lambda action: LoopObservation(
        content=f"{action.request.name}: {text}", source=ObservationSource.TOOL
    )


@dataclass
class Harness:
    gateway: ScriptedGateway
    bus: AgentRunEventBus
    events: RecordingSubscriber
    workspace: FakeSnapshotProvider
    context: WindowManager | None = None
    actions: list[LoopAction] = field(default_factory=list)

    def loop(self) -> BuiltinAgentLoop:
        return BuiltinAgentLoop(
            self.gateway,
            FakeMeter(),  # type: ignore[arg-type]
            rules=builtin_rules(
                context=self.context, workspace_provider=self.workspace
            ),
            model_transport_policy=ModelTransportPolicy(),
            event_bus=self.bus,
        )

    def run(
        self,
        loop_input: LoopInput,
        *,
        on_tool: ToolAnswer | None = None,
        max_steps: int = 50,
    ) -> tuple[LoopStop, BuiltinAgentLoop]:
        """跑到停为止. 回答确认与工具结果都由这里回填."""
        on_tool = on_tool or tool_ok()
        loop = self.loop()
        step = loop.start(loop_input)
        for _ in range(max_steps):
            if isinstance(step, LoopStop):
                return step, loop
            assert step is not None
            self.actions.append(step)
            if isinstance(step, AnswerAction):
                step = loop.observe(
                    LoopObservation(
                        content="answer_delivered", source=ObservationSource.CONTEXT
                    )
                )
            elif isinstance(step, ToolRequestAction):
                step = loop.observe(on_tool(step))
            else:
                raise AssertionError(f"未知动作 {step!r}")
        raise AssertionError("超过最大步数还没停")

    def tool_actions(self) -> list[ToolRequestAction]:
        return [a for a in self.actions if isinstance(a, ToolRequestAction)]


@pytest.fixture
def harness() -> Callable[..., Harness]:
    def build(*steps: Step, context: WindowManager | None = None) -> Harness:
        bus, recorder = recording_bus()
        return Harness(
            gateway=ScriptedGateway(*steps),
            bus=bus,
            events=recorder,
            workspace=FakeSnapshotProvider(),
            context=context,
        )

    return build
