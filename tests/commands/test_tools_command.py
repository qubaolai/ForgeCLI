"""/tools: 当前模式下模型能看见什么 (ADR-0007).

它必须走与模型完全相同的目录谓词. 用另一套过滤逻辑的话, "/tools 说 plan 档看不见
fs.edit_file"就不再是关于真实目录的陈述, 而只是一句好听的话.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from types import MappingProxyType

from rich.console import Console

from forgecli.application.tools.registry import ToolRegistry
from forgecli.application.tools.tool import Tool, ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.intents import SessionMode, SlashCommand
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.errors import PreparationError
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.tool.result import ToolResult
from forgecli.domain.tool.spec import TargetDeclarationAbility, ToolSpec
from forgecli.interfaces.cli.commands.tools_command import ToolsCommand
from forgecli.interfaces.cli.output import RichOutput
from forgecli.shared.cancellation import CancelToken

_EMPTY: Mapping[str, object] = MappingProxyType({})


class _Tool(Tool):
    def __init__(self, name: str, capability: Capability) -> None:
        self._spec = ToolSpec(
            name=name,
            version="1",
            title=name,
            description=name,
            input_schema=_EMPTY,
            output_schema=_EMPTY,
            declared_capabilities=frozenset({capability}),
            target_declaration_ability=TargetDeclarationAbility.STATIC,
            default_timeout_seconds=5.0,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    # /tools 只读 spec, 两段执行接口都不会被走到.
    def prepare(
        self, request: ToolInvocationRequest, context: ExecutionContext
    ) -> ToolPlan | PreparationError:  # pragma: no cover
        raise NotImplementedError

    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:  # pragma: no cover
        raise NotImplementedError


class _Snapshot:
    def __init__(self, mode: SessionMode) -> None:
        self.mode = mode


class _Session:
    def __init__(self, mode: SessionMode) -> None:
        self._mode = mode

    def current(self) -> _Snapshot:
        return _Snapshot(self._mode)


def _run(mode: SessionMode) -> str:
    registry = ToolRegistry()
    registry.register_all(
        (
            _Tool("fs.read_file", Capability.WORKSPACE_READ),
            _Tool("fs.edit_file", Capability.WORKSPACE_WRITE),
            _Tool("shell.run", Capability.EXECUTE_SHELL),
        )
    )
    console = Console(file=io.StringIO(), width=120, no_color=True)
    ToolsCommand(
        registry,
        _Session(mode),  # type: ignore[arg-type]
        RichOutput(console),
    ).execute(SlashCommand("/tools", "tools"))
    stream = console.file
    assert isinstance(stream, io.StringIO)
    return stream.getvalue()


def _section(text: str, header: str) -> str:
    """取某一节的正文, 用于区分"列在可见里"与"列在不可见里"."""
    _, _, rest = text.partition(header)
    return rest.split("本模式下不可见")[0] if header == "模型可见" else rest


def test_plan_mode_hides_mutating_tools() -> None:
    text = _run(SessionMode.PLAN)

    visible = _section(text, "模型可见")
    assert "fs.read_file" in visible
    assert "fs.edit_file" not in visible
    assert "shell.run" not in visible


def test_hidden_tools_are_listed_as_hidden_not_omitted() -> None:
    """ "看不见 fs.edit_file"与"没有这个工具"是两回事, 混在一起会被当成功能缺失."""
    text = _run(SessionMode.PLAN)

    hidden = _section(text, "本模式下不可见")
    assert "fs.edit_file" in hidden
    assert "shell.run" in hidden


def test_other_modes_see_everything() -> None:
    text = _run(SessionMode.ACCEPT_EDITS)

    assert "本模式下不可见" not in text
    for name in ("fs.read_file", "fs.edit_file", "shell.run"):
        assert name in text


def test_capabilities_are_labelled_as_an_upper_bound() -> None:
    """上界宽不等于这次调用危险. 不写清楚, /tools 就变成一张吓人的风险表."""
    text = _run(SessionMode.ACCEPT_EDITS)

    assert "上界" in text
    assert "逐次裁决" in text
