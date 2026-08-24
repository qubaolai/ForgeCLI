from __future__ import annotations

from pathlib import Path

from forgecli.application.tools.builtin.git_read import GitReadTool
from forgecli.application.tools.command_executor import (
    CommandExecutor,
    CommandOutcome,
    CommandRequest,
)
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.tool import ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.errors import PreparationError
from forgecli.domain.tool.plan import ToolPlan
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from forgecli.shared.cancellation import CancelToken
from support.fakes import PROFILE, NullArtifactStore


class _CapturingExecutor(CommandExecutor):
    def __init__(self) -> None:
        self.request: CommandRequest | None = None

    def run(
        self, request: CommandRequest, cancel: CancelToken | None = None
    ) -> CommandOutcome:
        self.request = request
        return CommandOutcome(exit_code=0, stdout="ok")


def _context(tmp_path: Path) -> ExecutionContext:
    root = tmp_path / "workspace"
    binary = tmp_path / "bin" / "git"
    root.mkdir()
    binary.parent.mkdir()
    binary.write_text("fake", encoding="utf-8")
    return ExecutionContext(
        cwd=str(root),
        workspace_roots=(str(root),),
        environment={"PATH": str(binary.parent), "HOME": str(tmp_path / "home")},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )


def _prepare(
    tool: GitReadTool,
    context: ExecutionContext,
    subcommand: str,
    args: list[str],
) -> ToolPlan | PreparationError:
    return tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-git",
            tool_name="git.read",
            arguments={"subcommand": subcommand, "args": args},
            tool_call_id="call-git",
        ),
        context,
    )


def test_remote_show_is_rejected_because_it_may_access_the_network(
    tmp_path: Path,
) -> None:
    executor = _CapturingExecutor()
    tool = GitReadTool(executor, ResourceGovernor(), NullArtifactStore())

    plan = _prepare(tool, _context(tmp_path), "remote", ["show", "origin"])

    assert isinstance(plan, PreparationError)


def test_git_execution_disables_prompts_pagers_locks_and_fsmonitor(
    tmp_path: Path,
) -> None:
    executor = _CapturingExecutor()
    tool = GitReadTool(executor, ResourceGovernor(), NullArtifactStore())
    context = _context(tmp_path)
    plan = _prepare(tool, context, "status", ["--short"])
    assert isinstance(plan, ToolPlan), plan

    tool.perform(plan, context)

    assert executor.request is not None
    assert executor.request.argv[1:5] == (
        "--no-pager",
        "-c",
        "core.fsmonitor=false",
        "status",
    )
    assert executor.request.environment["GIT_OPTIONAL_LOCKS"] == "0"
    assert executor.request.environment["GIT_TERMINAL_PROMPT"] == "0"
    assert executor.request.environment["GIT_PAGER"] == "cat"


def test_git_diff_forces_external_diff_and_textconv_off(tmp_path: Path) -> None:
    executor = _CapturingExecutor()
    tool = GitReadTool(executor, ResourceGovernor(), NullArtifactStore())
    context = _context(tmp_path)
    plan = _prepare(tool, context, "diff", [])
    assert isinstance(plan, ToolPlan), plan

    tool.perform(plan, context)

    assert executor.request is not None
    assert "--no-ext-diff" in executor.request.argv
    assert "--no-textconv" in executor.request.argv
