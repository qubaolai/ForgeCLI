"""shell_run 怎么把子进程的结局翻译成 ToolResult.

分界线是**工具有没有正常工作**, 不是**命令有没有干成**:

- 子进程跑到自然结束 -> OK. 退出码是这条命令的结果, 由模型自己判断.
- 超时 / 取消 / 起不来 -> 错误. 那才是工具没正常工作.

这条分界线来自一次真实任务: `grep` 无匹配退出 1 被标成 tool_error, 模型于是给每条命令
加 `|| echo "No matches found"` —— 三轮模型调用花在跟工具契约斗上, 而且后续命令更难解析.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.tools.builtin.shell_run import ShellRunTool
from forgecli.application.tools.command_executor import (
    CommandExecutor,
    CommandOutcome,
    CommandRequest,
)
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.tool import ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.errors import PreparationError, PreparationErrorCode
from forgecli.domain.tool.plan import ToolPlan
from forgecli.domain.tool.result import ToolResult, ToolResultStatus
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from forgecli.shared.cancellation import CancelToken
from support.fakes import PROFILE, NullArtifactStore


class ScriptedExecutor(CommandExecutor):
    """回放一个固定结局. 真跑子进程没法稳定造出超时与启动失败."""

    def __init__(self, outcome: CommandOutcome) -> None:
        self._outcome = outcome

    def run(
        self, request: CommandRequest, cancel: CancelToken | None = None
    ) -> CommandOutcome:
        return self._outcome


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root


def _result(workspace: Path, outcome: CommandOutcome) -> ToolResult:
    tool = ShellRunTool(
        ScriptedExecutor(outcome), ResourceGovernor(), NullArtifactStore()
    )
    context = ExecutionContext(
        cwd=str(workspace),
        workspace_roots=(str(workspace),),
        environment={"PATH": "/usr/bin:/bin"},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )
    plan = tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-1",
            tool_name="shell_run",
            arguments={"command": "grep -r x .", "shell_kind": "posix"},
            tool_call_id="c1",
        ),
        context,
    )
    assert isinstance(plan, ToolPlan), plan
    return tool.perform(plan, context)


# ---- 非零退出是结果, 不是错误 ----


def test_a_nonzero_exit_is_not_a_tool_error(workspace: Path) -> None:
    """grep 无匹配就是退出 1. 这是结论, 不是故障."""
    result = _result(workspace, CommandOutcome(exit_code=1))

    assert result.status is ToolResultStatus.OK
    assert result.error is None


def test_the_exit_code_reaches_the_model(workspace: Path) -> None:
    """退出码必须进正文.

    模型看不到 ToolMetrics —— 回填给它的只有 content_parts (ToolObservation.render
    直接取 ToolResult.text). 退出码只放进 metrics 等于没说.
    """
    result = _result(workspace, CommandOutcome(exit_code=1))

    assert "退出码 1" in result.text


def test_an_empty_nonzero_result_says_it_ran(workspace: Path) -> None:
    """空输出 + 非零退出最容易被误读成"命令挂了".

    模型此前收到的是一个空字符串加一个 is_error 标记, 分不清"确实没找到"和"没跑成".
    """
    result = _result(workspace, CommandOutcome(exit_code=1))

    assert "没有任何输出" in result.text
    assert result.text.strip() != ""


def test_a_zero_exit_does_not_clutter_the_output(workspace: Path) -> None:
    """成功的命令不加退出码噪音: 每条输出都缀一行"退出码 0"是在浪费上下文."""
    result = _result(workspace, CommandOutcome(exit_code=0, stdout="hello\n"))

    assert "退出码" not in result.text
    assert result.text == "hello\n"


def test_stderr_is_kept_alongside_stdout(workspace: Path) -> None:
    result = _result(
        workspace, CommandOutcome(exit_code=2, stdout="out", stderr="boom")
    )

    assert "out" in result.text
    assert "boom" in result.text
    assert "退出码 2" in result.text


def test_stderr_alone_still_shows_up(workspace: Path) -> None:
    result = _result(workspace, CommandOutcome(exit_code=1, stderr="no such file"))

    assert "no such file" in result.text
    assert "退出码 1" in result.text


# ---- 工具真的没跑成才是错误 ----


def test_a_timeout_is_still_an_error(workspace: Path) -> None:
    result = _result(workspace, CommandOutcome(exit_code=None, timed_out=True))

    assert result.status is ToolResultStatus.TIMEOUT
    assert result.error is not None


def test_a_cancellation_is_still_an_error(workspace: Path) -> None:
    result = _result(workspace, CommandOutcome(exit_code=None, cancelled=True))

    assert result.status is ToolResultStatus.CANCELLED
    assert result.error is not None


def test_a_failed_launch_is_still_an_error(workspace: Path) -> None:
    """子进程起不来: 有退出码也不算跑成了, failure 说了算."""
    result = _result(
        workspace, CommandOutcome(exit_code=127, failure="可执行文件不存在")
    )

    assert result.status is ToolResultStatus.TOOL_ERROR
    assert result.error is not None
    assert result.error.message == "可执行文件不存在"


def test_a_missing_exit_code_is_an_error(workspace: Path) -> None:
    """没有退出码就说不出"它跑完了", 哪怕没有任何一个失败标记."""
    result = _result(workspace, CommandOutcome(exit_code=None))

    assert result.status is ToolResultStatus.TOOL_ERROR


# ---- 退出码始终留在 metrics 里 ----


def test_metrics_keep_the_exit_code_for_the_terminal(workspace: Path) -> None:
    """终端进度行读的是 metrics, 与给模型的正文是两条通道, 都要有."""
    result = _result(workspace, CommandOutcome(exit_code=1))

    assert result.metrics.exit_code == 1


# ---- 字节数报的是截断前的量 ----


def test_bytes_out_counts_the_full_output_not_the_inlined_slice(
    workspace: Path,
) -> None:
    """输出被截断时, bytes_out 仍然是完整产出的字节数.

    两个数回答两个问题: bytes_out 说"这条命令产出了多少", content_parts 只是我们塞进
    上下文的那一段. 报截断后的量, 用户就看不出这次输出到底有多大, 而那恰恰是他判断
    "要不要去翻 artifact"的依据.
    """
    huge = "x" * (200 * 1024)
    result = _result(workspace, CommandOutcome(exit_code=0, stdout=huge))

    assert result.metrics.bytes_out == len(huge.encode("utf-8"))
    assert len(result.text.encode("utf-8")) < result.metrics.bytes_out
    assert any(part.truncated for part in result.content_parts)


def test_requested_shell_kind_must_match_the_actual_executor(
    workspace: Path,
) -> None:
    tool = ShellRunTool(
        ScriptedExecutor(CommandOutcome(exit_code=0)),
        ResourceGovernor(),
        NullArtifactStore(),
    )
    context = ExecutionContext(
        cwd=str(workspace),
        workspace_roots=(str(workspace),),
        environment={"PATH": "/usr/bin:/bin"},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )

    invalid = tool.prepare(
        ToolInvocationRequest(
            invocation_id="inv-wrong-shell",
            tool_name="shell_run",
            arguments={"command": "echo ok", "shell_kind": "powershell"},
            tool_call_id="c-wrong-shell",
        ),
        context,
    )

    assert isinstance(invalid, PreparationError)
    assert invalid.code is PreparationErrorCode.INVALID_INPUT


def test_executor_truncation_is_visible_to_the_model(workspace: Path) -> None:
    result = _result(
        workspace,
        CommandOutcome(exit_code=0, stdout="partial", truncated=True),
    )

    assert "可能不完整" in result.text
