from __future__ import annotations

import os
import sys

from forgecli.application.tools.command_executor import CommandRequest
from forgecli.infrastructure.execution.local_command_executor import (
    LocalCommandExecutor,
)


def test_stdout_and_stderr_share_one_output_budget() -> None:
    limit = 1000
    outcome = LocalCommandExecutor().run(
        CommandRequest(
            argv=(
                sys.executable,
                "-c",
                "import sys; print('o' * 2000); print('e' * 2000, file=sys.stderr)",
            ),
            cwd=os.getcwd(),
            environment={"PATH": os.environ.get("PATH", "")},
            timeout_seconds=5,
            max_output_bytes=limit,
        )
    )

    assert outcome.exit_code == 0
    assert outcome.truncated is True
    assert (
        len(outcome.stdout.encode("utf-8")) + len(outcome.stderr.encode("utf-8"))
        <= limit
    )
