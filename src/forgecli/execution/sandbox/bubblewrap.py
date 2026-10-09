"""Linux 与 WSL2 沙箱: bubblewrap.

与 Seatbelt 的关键差别: bubblewrap 建的是新的 mount namespace, 可见性靠逐项 bind
决定, 因此读边界天然是 allowlist. 受保护路径用 `--tmpfs` 盖住 —— 进程看到的是
一个空目录, 不是"读被拒", 连存在性都问不出来.

`--unshare-pid` 顺带把进程操作也关住了, 所以 Linux 上 `kill` / `pkill` 打不到宿主进程.
Seatbelt 没有这个能力, 那边只能靠"围栏外不可回滚操作表"兜底.

网络 (与 sandbox-runtime 的 Linux 桥同): `--unshare-net` 之后围栏里没有任何网络, 代理
在宿主上监听一个 unix socket, bind 进围栏, 围栏内起一个 `socat` 把 `127.0.0.1:3128` 转到
那个 socket, 子进程的 HTTP_PROXY 指向 3128. 没有 `socat` 就没有出口 —— 不退化成放开网络.

可写区里挖掉的路径 (`.git/hooks`) 与 denied 读区里放回的路径 (allowRead) 都靠后面的
bind 覆盖前面的: `--ro-bind` 叠在 `--bind` 之上, `--ro-bind` 叠在 `--tmpfs` 之上. 只对
存在的路径做 —— bind 一个不存在的路径 bwrap 直接报错.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

from forgecli.core.execution.fence import FencePolicy
from forgecli.execution.sandbox.provider import (
    SandboxProvider,
    SelfTestReport,
    proxy_environment,
)

__all__ = ["BubblewrapProvider", "bwrap_filesystem_flags"]

_SELF_TEST_TIMEOUT = 15.0
# 围栏内代理桥监听的端口 (对应 sandbox-runtime 的内部 3128).
_BRIDGE_PORT = 3128


def bwrap_filesystem_flags(
    policy: FencePolicy,
    *,
    translate: Callable[[str], str] = lambda path: path,
    resolve: Callable[[str], str] | None = None,
) -> list[str]:
    """可写根, 挖掉的写路径, 盖住的读路径, 放回的读路径, 按这个顺序: 后面的覆盖前面的.

    `resolve` 解符号链接 (缺省 realpath); `translate` 给 WSL2 把 Windows 路径翻成
    `/mnt/c/...`, 那边不能 realpath (宿主的 realpath 会把 WSL 侧的路径当成 Windows
    路径).
    """
    real = resolve or _real
    flags: list[str] = []
    for root in policy.all_writable:
        if root:
            path = translate(real(root))
            flags += ["--bind", path, path]
    for denied in policy.denied_write_paths:
        if denied and os.path.exists(real(denied)):
            path = translate(real(denied))
            flags += ["--ro-bind", path, path]
    for denied in policy.denied_read_paths:
        if denied:
            # tmpfs 盖住: 进程看到空目录, 问不出原内容, 也问不出存在性.
            flags += ["--tmpfs", translate(real(denied))]
    for allowed in policy.allowed_read_paths:
        if allowed and os.path.exists(real(allowed)):
            path = translate(real(allowed))
            flags += ["--ro-bind", path, path]
    return flags


def _real(path: str) -> str:
    try:
        return os.path.realpath(path)
    except OSError:
        return path


class BubblewrapProvider(SandboxProvider):
    def __init__(self, executable: str | None = None) -> None:
        self._executable = executable or shutil.which("bwrap") or ""
        self._socat = shutil.which("socat") or ""

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
        flags += bwrap_filesystem_flags(policy)
        network = policy.network
        if network.unrestricted:
            return (*flags, "--", *argv)
        flags.append("--unshare-net")
        if network.proxy_socket and self._socat:
            # 代理的 unix socket bind 进来, 围栏内 socat 把 127.0.0.1:3128 转过去.
            flags += ["--bind", network.proxy_socket, network.proxy_socket]
            bridge = (
                f"{shlex.quote(self._socat)} "
                f"TCP-LISTEN:{_BRIDGE_PORT},bind=127.0.0.1,fork,reuseaddr "
                f"UNIX-CONNECT:{shlex.quote(network.proxy_socket)} & "
                'exec "$@"'
            )
            return (*flags, "--", "/bin/sh", "-c", bridge, "sh", *argv)
        return (*flags, "--", *argv)

    def environment_for(self, policy: FencePolicy) -> Mapping[str, str]:
        env: dict[str, str] = {}
        if policy.instance_temp_root:
            temp = _real(policy.instance_temp_root)
            env.update({"TMPDIR": temp, "TMP": temp, "TEMP": temp})
        network = policy.network
        if network.proxied and network.proxy_socket and self._socat:
            env.update(proxy_environment(f"http://localhost:{_BRIDGE_PORT}"))
        return env

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
