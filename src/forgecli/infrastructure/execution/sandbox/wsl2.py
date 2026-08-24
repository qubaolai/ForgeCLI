"""Windows 上的围栏: 经 WSL2 借用 bubblewrap (ADR-0030 决策 5, 2026-08-24 修订).

原生 Windows 没有可用的围栏原语: AppContainer 要求应用自己带 manifest 与 SID, 不适用于
"任意用户命令"; Job Object 管的是资源不是文件系统边界. 所以这里不自己造, 而是**把命令
交给 WSL2 里的 bubblewrap** —— 那是 Linux 内核的 mount namespace, 与 Linux 上完全同一
套保证.

代价要说清楚, 这不只是安全机制的变化:

- 命令跑在 **Linux** 里. `python` / `node` / `git` 是 WSL 里那一套, 不是 Windows 上的.
- 路径要翻译: `C:\\Users\\x\\proj` -> `/mnt/c/Users/x/proj`.
- `.exe` 与 PowerShell 脚本在这条路径上跑不了.

因此 WSL2 未安装时**不退化成"用静态分析补偿"**, 而是直接 UNCONFINED: 工作区内照常,
跨栏一律 ASK (ADR-0030 决策 5). 那条路不需要任何命令知识 —— budget 里
EXECUTE_SHELL 只在 confined 时进自动集合, 所以无围栏时每条 shell 命令必然落 ASK.

**未在真实 Windows 上验证.** 本机是 macOS, 只有纯函数部分有测试覆盖. 这一点是可控的:
`available()` 与 `self_test()` 任一不过就落回 NoSandbox, 而那正是用户选定的兜底行为 ——
实现有错的后果是"退回每条命令都问", 不是"以为有围栏而其实没有".
"""

from __future__ import annotations

import re
import shutil
import subprocess

from forgecli.application.tools.sandbox_provider import SandboxProvider, SelfTestReport
from forgecli.domain.execution.fence import FencePolicy

__all__ = ["Wsl2Provider", "windows_to_wsl_path", "wsl2_bubblewrap_available"]

_PROBE_TIMEOUT = 20.0
_SELF_TEST_TIMEOUT = 30.0
_DRIVE = re.compile(r"^([A-Za-z]):[\\/](.*)$", re.DOTALL)


def windows_to_wsl_path(path: str) -> str:
    """`C:\\Users\\x\\proj` -> `/mnt/c/Users/x/proj`.

    已经是 Linux 形态的路径原样返回 —— 组合根在某些路径上传的就是 WSL 侧的路径,
    再翻译一次会得到 `/mnt//home/...`.
    """
    if not path:
        return path
    if path.startswith("/"):
        return path
    match = _DRIVE.match(path)
    if match is None:
        # UNC 或相对路径: 翻不了就原样给出去, 让 bwrap 报错. 猜一个路径比报错危险 ——
        # 猜错的那个可能正好是个存在的目录.
        return path.replace("\\", "/")
    drive, rest = match.groups()
    return f"/mnt/{drive.lower()}/" + rest.replace("\\", "/")


def _wsl_executable() -> str:
    return shutil.which("wsl.exe") or shutil.which("wsl") or ""


def wsl2_bubblewrap_available() -> bool:
    """WSL2 装了, 有一个 version 2 的发行版, 而且里面有可用的 bwrap.

    三件事都要实测. 只看 `wsl.exe` 在不在会把 WSL1 (没有真内核, 没有 namespace) 与
    "装了但没有发行版"都算成可用.
    """
    executable = _wsl_executable()
    if not executable:
        return False
    try:
        listed = subprocess.run(  # noqa: S603 - argv 固定, 无 shell
            [executable, "-l", "-v"],
            capture_output=True,
            timeout=_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if listed.returncode != 0:
        return False
    # wsl -l -v 的输出是 UTF-16LE.
    text = listed.stdout.decode("utf-16-le", errors="replace")
    if not any(line.rstrip().endswith("2") for line in text.splitlines()[1:]):
        return False
    try:
        probe = subprocess.run(  # noqa: S603 - argv 固定, 无 shell
            [executable, "-e", "bwrap", "--version"],
            capture_output=True,
            timeout=_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


class Wsl2Provider(SandboxProvider):
    def __init__(self, executable: str | None = None) -> None:
        self._executable = executable if executable is not None else _wsl_executable()

    @property
    def name(self) -> str:
        return "wsl2_bubblewrap"

    def available(self) -> bool:
        return bool(self._executable) and wsl2_bubblewrap_available()

    def wrap(self, argv: tuple[str, ...], policy: FencePolicy) -> tuple[str, ...]:
        """把 `wsl.exe -e <cmd...>` 改写成 `wsl.exe -e bwrap <flags> -- <cmd...>`.

        形状对不上就抛错而不是硬拼: 这条路径上一次拼错的后果是命令在**没有围栏**的
        WSL 里跑起来, 而调用方会以为它被围住了.
        """
        if not argv:
            raise OSError("argv 为空, 无法包进围栏")
        if not self._executable:
            raise OSError("wsl.exe 不可用")
        if len(argv) < 3 or argv[1] != "-e":
            raise OSError(
                f"WSL2 围栏要求 argv 形如 `wsl.exe -e <程序> ...`, 实际是 {argv[:2]}"
            )
        inner = argv[2:]
        return (self._executable, "-e", *self._bwrap_flags(policy), "--", *inner)

    def _bwrap_flags(self, policy: FencePolicy) -> tuple[str, ...]:
        flags: list[str] = [
            "bwrap",
            # 真实根只读可见, 再把可写根按原路径 bind 回来. 顺序要紧: 后面的覆盖前面的.
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
            "--die-with-parent",
            # PID namespace 让 kill / pkill 打不到 WSL 里的其他进程.
            "--unshare-pid",
        ]
        for root in policy.all_writable:
            if root:
                translated = windows_to_wsl_path(root)
                flags += ["--bind", translated, translated]
        for path in policy.denied_read_paths:
            if path:
                # tmpfs 盖住: 进程看到空目录, 问不出原内容, 也问不出存在性.
                flags += ["--tmpfs", windows_to_wsl_path(path)]
        if not policy.network_allowed:
            flags.append("--unshare-net")
        return tuple(flags)

    def self_test(self) -> SelfTestReport:
        if not self.available():
            return SelfTestReport(
                provider=self.name, failures=("WSL2 或 bwrap 不可用",)
            )
        return self._run_self_test()

    def _run_self_test(self) -> SelfTestReport:
        """在 WSL 侧建临时目录做三次探测.

        全程在 Linux 里完成, 不碰 Windows 文件系统 —— 这样探测的就是围栏本身, 不掺进
        路径翻译对不对.
        """
        script = (
            "set -e; "
            "root=$(mktemp -d); ws=$root/ws; secret=$root/secret; "
            "mkdir -p $ws $secret; echo s > $secret/cred; echo $root"
        )
        prepared = self._wsl_output(script)
        if prepared is None:
            return SelfTestReport(
                provider=self.name, failures=("无法在 WSL 里建临时目录",)
            )
        root = prepared.strip()
        policy = FencePolicy(
            writable_roots=(f"{root}/ws",),
            denied_read_paths=(f"{root}/secret",),
            network_allowed=False,
        )
        failures: list[str] = []

        wrote_outside = self._fenced_succeeds(policy, f"echo x > {root}/escaped.txt")
        if wrote_outside:
            failures.append("边界外写入没有被拒绝")
        read_secret = self._fenced_succeeds(policy, f"cat {root}/secret/cred")
        if read_secret:
            failures.append("受保护路径读取没有被拒绝")
        reached = self._fenced_succeeds(
            policy, "curl -s -m 5 -o /dev/null https://example.com"
        )
        if reached:
            failures.append("网络连接没有被拒绝")
        if not self._fenced_succeeds(policy, f"echo ok > {root}/ws/probe.txt"):
            failures.append("围栏内写入被误拒, 参数不可用")

        self._wsl_output(f"rm -rf {root}")
        return SelfTestReport(
            provider=self.name,
            outside_write_blocked=not wrote_outside,
            protected_read_blocked=not read_secret,
            network_blocked=not reached,
            failures=tuple(failures),
        )

    def _wsl_output(self, script: str) -> str | None:
        try:
            completed = subprocess.run(  # noqa: S603 - argv 由本模块构造, 无 shell
                [self._executable, "-e", "/bin/sh", "-c", script],
                capture_output=True,
                timeout=_SELF_TEST_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0:
            return None
        return completed.stdout.decode("utf-8", errors="replace")

    def _fenced_succeeds(self, policy: FencePolicy, script: str) -> bool:
        argv = self.wrap((self._executable, "-e", "/bin/sh", "-c", script), policy)
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
