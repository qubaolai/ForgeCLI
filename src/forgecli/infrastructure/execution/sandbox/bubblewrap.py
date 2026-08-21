"""Linux 与 WSL2 围栏: bubblewrap (ADR-0030 决策 2).

与 Seatbelt 的关键差别: bubblewrap 建的是新的 mount namespace, 可见性靠逐项 bind
决定, 因此**读边界天然是 allowlist**. 受保护路径用 `--tmpfs` 盖住 —— 进程看到的是
一个空目录, 不是"读被拒", 连存在性都问不出来.

这就是 ADR-0030 记录的那处平台不对称: 同一条安全性质, Linux 由内核的 namespace 保证,
macOS 只能靠一张需要追赶的 denylist.

`--unshare-pid` 顺带把进程操作也关住了, 所以 Linux 上 `kill` / `pkill` 打不到宿主进程.
Seatbelt 没有这个能力, 那边只能靠"围栏外不可回滚操作表"兜底.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from forgecli.application.tools.sandbox_provider import SandboxProvider, SelfTestReport
from forgecli.domain.execution.fence import FencePolicy

__all__ = ["BubblewrapProvider"]

_SELF_TEST_TIMEOUT = 15.0


def _real(path: str) -> str:
    try:
        return os.path.realpath(path)
    except OSError:
        return path


class BubblewrapProvider(SandboxProvider):
    def __init__(self, executable: str | None = None) -> None:
        self._executable = executable or shutil.which("bwrap") or ""

    @property
    def name(self) -> str:
        return "bubblewrap"

    def available(self) -> bool:
        if not self._executable:
            return False
        # 装了不等于能用: 很多发行版要 unprivileged userns, 而它可能被 sysctl 关掉.
        try:
            completed = subprocess.run(  # noqa: S603 - argv 固定, 无 shell
                [self._executable, "--ro-bind", "/", "/", "--", "/bin/true"],
                capture_output=True,
                timeout=_SELF_TEST_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0

    def wrap(self, argv: tuple[str, ...], policy: FencePolicy) -> tuple[str, ...]:
        if not argv:
            raise OSError("argv 为空, 无法包进围栏")
        if not self._executable:
            raise OSError("bwrap 不可用")
        flags: list[str] = [
            self._executable,
            # 整棵真实根只读可见, 再把可写根按原路径 bind 回来. 顺序要紧: 后面的 bind
            # 覆盖前面的.
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
            # 子进程死在父进程之前, 不留孤儿.
            "--die-with-parent",
            # PID namespace 让 kill / pkill 打不到宿主进程.
            "--unshare-pid",
        ]
        for root in policy.all_writable:
            if root:
                real = _real(root)
                flags += ["--bind", real, real]
        for path in policy.denied_read_paths:
            if path:
                # tmpfs 盖住: 进程看到空目录, 问不出原内容, 也问不出存在性.
                flags += ["--tmpfs", _real(path)]
        if not policy.network_allowed:
            flags.append("--unshare-net")
        return (*flags, "--", *argv)

    def self_test(self) -> SelfTestReport:
        if not self.available():
            return SelfTestReport(provider=self.name, failures=("bwrap 不可用",))
        root = Path(tempfile.mkdtemp(prefix="forge-fence-selftest-"))
        try:
            return self._run_self_test(root)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def _run_self_test(self, root: Path) -> SelfTestReport:
        workspace = root / "ws"
        workspace.mkdir()
        secret_dir = root / "secret"
        secret_dir.mkdir()
        secret = secret_dir / "credential"
        secret.write_text("forge-self-test-secret\n", encoding="utf-8")
        outside = root / "escaped.txt"
        policy = FencePolicy(
            writable_roots=(str(workspace),),
            denied_read_paths=(str(secret_dir),),
            network_allowed=False,
        )
        failures: list[str] = []

        wrote_outside = self._shell_succeeds(policy, f"echo x > {outside}")
        if wrote_outside or outside.exists():
            failures.append("边界外写入没有被拒绝")
        read_secret = self._shell_succeeds(policy, f"cat {secret}")
        if read_secret:
            failures.append("受保护路径读取没有被拒绝")
        reached = self._shell_succeeds(
            policy, "curl -s -m 5 -o /dev/null https://example.com"
        )
        if reached:
            failures.append("网络连接没有被拒绝")
        if not self._shell_succeeds(policy, f"echo ok > {workspace / 'probe.txt'}"):
            failures.append("围栏内写入被误拒, 参数不可用")

        return SelfTestReport(
            provider=self.name,
            outside_write_blocked=not wrote_outside and not outside.exists(),
            protected_read_blocked=not read_secret,
            network_blocked=not reached,
            failures=tuple(failures),
        )

    def _shell_succeeds(self, policy: FencePolicy, script: str) -> bool:
        argv = self.wrap(("/bin/sh", "-c", script), policy)
        try:
            completed = subprocess.run(  # noqa: S603 - argv 由本模块构造, 无 shell
                list(argv),
                capture_output=True,
                timeout=_SELF_TEST_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0
