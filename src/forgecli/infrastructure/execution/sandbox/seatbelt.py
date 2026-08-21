"""macOS 围栏: sandbox-exec + Seatbelt profile (ADR-0030 决策 2).

profile 的形态是**实测定下来的**, 不是照抄别处的写法 (ADR-0030 实测记录第二节):

    (deny default) + 逐项 allow          -> 子进程 SIGABRT, 连 /bin/sh 都起不来
    (allow default) + (deny file-read*)
      + 系统目录 allowlist                -> 仍然 SIGABRT, python3 也起不来
    (allow default) + 定向 deny           -> 全部按预期工作

所以写边界是 allowlist (`deny file-write*` 再逐项放行), 读边界只能是 denylist.
后者意味着受保护路径要逐条列举, **漏一条就是凭证暴露** —— 这条风险在 macOS 上没有解,
Linux 的 bubblewrap 靠 mount namespace 不受此限.

路径一律先 `realpath`: macOS 上 `/tmp` 是 `/private/tmp` 的符号链接, 而 Seatbelt 比对
的是解析后的真实路径. 拿没解析的路径去写 profile, 规则会静默不匹配 —— 那比报错糟得多.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from forgecli.application.tools.sandbox_provider import SandboxProvider, SelfTestReport
from forgecli.domain.execution.fence import FencePolicy

__all__ = ["SeatbeltProvider"]

_SANDBOX_EXEC = "/usr/bin/sandbox-exec"
_SELF_TEST_TIMEOUT = 15.0

# 任何命令都要能写的字符设备. 少了它们连 `echo x > /dev/null` 都会失败.
_DEVICE_WRITES = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty")


def _quote(path: str) -> str:
    """Seatbelt profile 用的是 TinyScheme 字面量, 转义规则与它一致."""
    escaped = path.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _real(path: str) -> str:
    """解析符号链接. 解析不了就原样返回 —— 让规则不匹配, 好过让整条 profile 报错."""
    try:
        return os.path.realpath(path)
    except OSError:
        return path


class SeatbeltProvider(SandboxProvider):
    @property
    def name(self) -> str:
        return "seatbelt"

    def available(self) -> bool:
        if not Path(_SANDBOX_EXEC).exists():
            return False
        # 存在不等于能用: 某些受限环境下 sandbox-exec 自己就会被拒. 实跑一次最便宜的
        # 全放行 profile 确认它真的能起进程.
        try:
            completed = subprocess.run(  # noqa: S603 - argv 固定, 无 shell
                [_SANDBOX_EXEC, "-p", "(version 1)(allow default)", "/usr/bin/true"],
                capture_output=True,
                timeout=_SELF_TEST_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0

    def profile_for(self, policy: FencePolicy) -> str:
        lines = ["(version 1)", "(allow default)", "(deny file-write*)"]
        writable = [_real(root) for root in policy.all_writable if root]
        allow_write = [f"(subpath {_quote(root)})" for root in writable]
        allow_write += [f"(literal {_quote(dev)})" for dev in _DEVICE_WRITES]
        # /dev/fd 下的重定向目标是按需创建的, 只能按 subpath 放行.
        allow_write.append('(subpath "/dev/fd")')
        lines.append(f"(allow file-write* {' '.join(allow_write)})")
        denied = [_real(path) for path in policy.denied_read_paths if path]
        if denied:
            targets = " ".join(f"(subpath {_quote(path)})" for path in denied)
            lines.append(f"(deny file-read* {targets})")
        if not policy.network_allowed:
            lines.append("(deny network*)")
        return "\n".join(lines) + "\n"

    def wrap(self, argv: tuple[str, ...], policy: FencePolicy) -> tuple[str, ...]:
        if not argv:
            raise OSError("argv 为空, 无法包进围栏")
        # 用 -p 传字面 profile 而不是写临时文件: 少一个需要清理的文件, 也少一个别人
        # 能在 exec 之前改掉它的窗口.
        return (_SANDBOX_EXEC, "-p", self.profile_for(policy), *argv)

    def self_test(self) -> SelfTestReport:
        """真的去做三次被禁止的操作 (ADR-0030 决策 3)."""
        if not self.available():
            return SelfTestReport(provider=self.name, failures=("sandbox-exec 不可用",))
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
        # 连一个必然存在且不会被防火墙静默丢弃的地址; 拒绝时 curl 立刻返回非零.
        reached = self._shell_succeeds(
            policy, "curl -s -m 5 -o /dev/null https://example.com"
        )
        if reached:
            failures.append("网络连接没有被拒绝")

        # 反向确认: 围栏内的写必须成功. 少了这一步, 一个"什么都拦"的坏 profile 会被
        # 当成完美围栏.
        if not self._shell_succeeds(policy, f"echo ok > {workspace / 'probe.txt'}"):
            failures.append("围栏内写入被误拒, profile 不可用")

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
