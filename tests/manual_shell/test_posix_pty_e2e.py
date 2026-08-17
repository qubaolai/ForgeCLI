"""真伪终端下的 POSIX Provider (ADR-0017 §15.2, §15.5).

ADR 明写"需要新增伪终端 E2E 测试, 普通 StringIO 单元测试不足以覆盖信号, job control
和终端恢复". 所以这里真的开一个 pty, 真的 fork, 真的跑 `/bin/sh`.

做法: ``pty.fork()`` 的子进程拿到一个**控制终端**, 在那里面调 Provider —— 这正是产品里
的处境 (Forge 的 stdin/stdout 就是用户终端). 父进程扮演"用户", 往 pty 里喂命令, 读回显.

子进程通过退出码回报断言结果, 因为它的 stdout 就是被测的那个终端, 往里面 print 会和
Shell 输出混在一起.
"""

from __future__ import annotations

import os
import pty
import sys
from pathlib import Path

import pytest

if sys.platform == "win32":  # pragma: no cover - Windows 走另一个 Provider
    pytest.skip("POSIX 专属", allow_module_level=True)

_SH = "/bin/sh"
pytestmark = pytest.mark.skipif(
    not os.path.exists(_SH), reason=f"没有 {_SH}, 无法跑真实交互式 Shell"
)

# 子进程里跑的探针. 退出码即断言结果, 见各常量.
_OK = 0
_NOT_STARTED = 21
_WRONG_EXIT = 22
_WRONG_CWD = 23

_PROBE = """
import os, sys
sys.path.insert(0, {src!r})
from forgecli.domain.manual_shell.request import ManualShellRequest
from forgecli.infrastructure.manual_shell.posix_pty import (
    PosixInteractiveShellProvider,
)

result = PosixInteractiveShellProvider().open(
    ManualShellRequest(
        shell_executable={sh!r},
        argv=({sh!r}, "-c", {script!r}),
        cwd={cwd!r},
        environment=dict(os.environ),
    )
)
if not result.started:
    sys.exit({not_started})
if not ({assertion}):
    sys.exit({wrong_exit})
sys.exit(0)
"""


def _run_probe(script: str, *, cwd: str, assertion: str) -> tuple[int, str]:
    """在真 pty 里跑一次 Provider. 返回 (子进程退出码, pty 上看到的输出).

    assertion 是一段在子进程里对 ``result`` 求值的表达式 —— 断言必须在那边做, 因为
    ManualShellResult 回不到父进程 (中间隔着一个 fork).
    """
    source = _PROBE.format(
        src=str(Path(__file__).resolve().parents[2] / "src"),
        sh=_SH,
        script=script,
        cwd=cwd,
        assertion=assertion,
        not_started=_NOT_STARTED,
        wrong_exit=_WRONG_EXIT,
    )
    pid, master = pty.fork()
    if pid == 0:  # 子进程: 它的 stdin/stdout 就是 pty slave, 且它是会话首进程
        try:
            os.execv(sys.executable, [sys.executable, "-c", source])
        finally:  # pragma: no cover - execv 成功就不会到这
            os._exit(99)

    output = bytearray()
    try:
        while True:
            try:
                chunk = os.read(master, 4096)
            except OSError:  # slave 全部关闭后读会抛
                break
            if not chunk:
                break
            output.extend(chunk)
    finally:
        os.close(master)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), output.decode("utf-8", "replace")


def test_a_real_shell_runs_on_the_controlling_terminal(tmp_path: Path) -> None:
    """最基本的一条: Provider 真的能起一个 Shell 并拿回退出码."""
    code, output = _run_probe(
        "echo forge-pty-ok", cwd=str(tmp_path), assertion="result.exit_code == 0"
    )
    assert code == _OK, f"探针退出码 {code}, pty 输出: {output!r}"
    assert "forge-pty-ok" in output


def test_the_shell_sees_a_tty(tmp_path: Path) -> None:
    """§8.1: 必须是真终端, 不是管道. `test -t 0` 在管道里会失败 —— 而管道正是
    ADR §8.2 禁止降级成的那种形态."""
    code, output = _run_probe(
        "test -t 0 && test -t 1",
        cwd=str(tmp_path),
        assertion="result.exit_code == 0",
    )
    assert code == _OK, f"子 Shell 没拿到 TTY, pty 输出: {output!r}"


def test_the_shell_has_its_own_process_group(tmp_path: Path) -> None:
    """§7: 子 Shell 必须在**独立进程组**里, 否则 Ctrl-C 会同时打到 Forge ——
    用户想中断的是自己那条命令."""
    code, output = _run_probe(
        'test "$(ps -o pgid= -p $$ | tr -d \' \')" != "$(ps -o pgid= -p $PPID'
        " | tr -d ' ')\"",
        cwd=str(tmp_path),
        assertion="result.exit_code == 0",
    )
    assert code == _OK, f"子 Shell 与父进程同组, pty 输出: {output!r}"


def test_the_shell_starts_in_the_requested_cwd(tmp_path: Path) -> None:
    """§6.2: 初始 cwd 是 Forge 的 cwd."""
    (tmp_path / "marker.txt").write_text("x", encoding="utf-8")
    code, output = _run_probe(
        "test -f marker.txt", cwd=str(tmp_path), assertion="result.exit_code == 0"
    )
    assert code == _OK, f"cwd 不对, pty 输出: {output!r}"


def test_cd_and_export_live_inside_the_session(tmp_path: Path) -> None:
    """§15.2: cd 与 export 在当前子 Shell 内持续有效.

    同一个 Shell 进程里连续执行, 正是"真实连续会话"与"每条命令一个新子进程"的区别.
    """
    (tmp_path / "sub").mkdir()
    code, output = _run_probe(
        'cd sub && export FORGE_PROBE=1 && test "$(basename $PWD)" = sub'
        ' && test "$FORGE_PROBE" = 1',
        cwd=str(tmp_path),
        assertion="result.exit_code == 0",
    )
    assert code == _OK, f"cd/export 没保持, pty 输出: {output!r}"


def test_a_nonzero_exit_comes_back_as_a_code_not_an_error(tmp_path: Path) -> None:
    """非零退出是**用户命令**的结果, 不是 Forge 的故障 (§4)."""
    code, output = _run_probe(
        "exit 3", cwd=str(tmp_path), assertion="result.exit_code == 3"
    )
    assert code == _OK, f"退出码没传回来, pty 输出: {output!r}"


def test_a_signal_death_is_reported_as_a_signal(tmp_path: Path) -> None:
    """被信号杀掉时 exit_code 是 None, signal 有值.

    Popen 用负数编码信号 —— 那是实现细节, "exit -15" 对用户没有意义.
    """
    code, output = _run_probe(
        "kill -TERM $$",
        cwd=str(tmp_path),
        assertion="result.signal == 15 and result.exit_code is None",
    )
    assert code == _OK, f"信号没被识别, pty 输出: {output!r}"


def test_the_provider_refuses_without_a_terminal(tmp_path: Path) -> None:
    """§8.2: 平台缺少所需终端能力时明确拒绝, 不降级成伪交互管道.

    这条在**当前进程**里跑就够 —— pytest 捕获下 stdin/stdout 都不是 TTY, 正是要拒绝
    的那种环境.
    """
    from forgecli.application.manual_shell import ManualShellUnavailable
    from forgecli.domain.manual_shell.request import ManualShellRequest
    from forgecli.infrastructure.manual_shell.posix_pty import (
        PosixInteractiveShellProvider,
    )

    request = ManualShellRequest(
        shell_executable=_SH, argv=(_SH, "-c", "true"), cwd=str(tmp_path)
    )
    with pytest.raises(ManualShellUnavailable, match="终端"):
        PosixInteractiveShellProvider().open(request)
