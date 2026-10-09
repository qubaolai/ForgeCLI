"""macOS 沙箱: sandbox-exec + Seatbelt profile .

"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

from forgecli.core.execution.fence import FencePolicy
from forgecli.execution.sandbox.provider import (
    SandboxProvider,
    SelfTestReport,
    proxy_environment,
)

__all__ = ["SeatbeltProvider"]

_SANDBOX_EXEC = "/usr/bin/sandbox-exec"
_SELF_TEST_TIMEOUT = 15.0

# 任何命令都要能写的字符设备. 少了它们连 `echo x > /dev/null` 都会失败.
_DEVICE_WRITES = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty")


def _quote(path: str) -> str:
    """安全转义函数, mac沙箱 用的是 TinyScheme 字面量."""
    escaped = path.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _real(path: str) -> str:
    """解析符号链接. 解析不了就原样返回 —— 让规则不匹配, 好过让整条 profile 报错."""
    try:
        return os.path.realpath(path)
    except OSError:
        return path


def _subpaths(paths: list[str]) -> str:
    return " ".join(f"(subpath {_quote(path)})" for path in paths)


class SeatbeltProvider(SandboxProvider):
    @property
    def name(self) -> str:
        return "seatbelt"

    def available(self) -> bool:
        # 存在性检查
        if not Path(_SANDBOX_EXEC).exists():
            return False
        try:
            # 宽沙箱策略 执行探针
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
        # 通过沙箱围栏策略 生成沙箱配置文件
        lines = ["(version 1)", "(allow default)", "(deny file-write*)"] # 默认允许一切, 写操作禁止, 读、网络都允许
        writable = [_real(root) for root in policy.all_writable if root]
        allow_write = [f"(subpath {_quote(root)})" for root in writable]
        allow_write += [f"(literal {_quote(dev)})" for dev in _DEVICE_WRITES]
        # /dev/fd 下的重定向目标是按需创建的, 只能按 subpath 放行.
        allow_write.append('(subpath "/dev/fd")')
        lines.append(f"(allow file-write* {' '.join(allow_write)})")
        # 可写区里再挖掉的策略禁止写的路径, .sb文件语法 按顺序求值, 后面覆盖前面
        denied_write = [_real(path) for path in policy.denied_write_paths if path]
        if denied_write:
            lines.append(f"(deny file-write* {_subpaths(denied_write)})")
        denied = [_real(path) for path in policy.denied_read_paths if path]
        if denied:
            lines.append(f"(deny file-read* {_subpaths(denied)})")
        allowed_read = [_real(path) for path in policy.allowed_read_paths if path]
        if allowed_read:
            lines.append(f"(allow file-read* {_subpaths(allowed_read)})")
        lines.extend(self._network_rules(policy))
        return "\n".join(lines) + "\n"

    @staticmethod
    def _network_rules(policy: FencePolicy) -> list[str]:
        network = policy.network
        if network.unrestricted:
            return []
        rules = ["(deny network*)"]
        if network.proxy_port is not None:
            # 只放到代理那一条. 域名对不对由代理裁, 内核只管"除了它谁都连不上".
            rules.append(
                f'(allow network-outbound (remote ip "localhost:{network.proxy_port}"))'
            )
        if network.allow_local_binding:
            # bind / inbound 用 "*:*" 而不是 "localhost:*": 现代 macOS 把 IPv4 映射成
            # ::ffff:127.0.0.1, Seatbelt 的 localhost 过滤器认不出它 (sandbox-runtime
            # 的实测); outbound 仍限 localhost, 域名白名单不因此失效.
            rules.append('(allow network-bind (local ip "*:*"))')
            rules.append('(allow network-inbound (local ip "*:*"))')
            rules.append('(allow network-outbound (remote ip "localhost:*"))')
        if network.allow_all_unix_sockets:
            rules.append('(allow network-bind (local unix-socket (path-regex #"^/")))')
            rules.append(
                '(allow network-outbound (remote unix-socket (path-regex #"^/")))'
            )
        else:
            for path in network.allow_unix_sockets:
                real = _quote(_real(path))
                rules.append(
                    f"(allow network-bind (local unix-socket (subpath {real})))"
                )
                rules.append(
                    f"(allow network-outbound (remote unix-socket (subpath {real})))"
                )
        return rules

    def environment_for(self, policy: FencePolicy) -> Mapping[str, str]:
        env: dict[str, str] = {}
        if policy.instance_temp_root:
            temp = _real(policy.instance_temp_root)
            env.update({"TMPDIR": temp, "TMP": temp, "TEMP": temp})
        network = policy.network
        if network.proxied and network.proxy_port is not None:
            env.update(proxy_environment(f"http://localhost:{network.proxy_port}"))
        return env

    def wrap(self, argv: tuple[str, ...], policy: FencePolicy) -> tuple[str, ...]:
        if not argv:
            raise OSError("argv 为空, 无法包进沙箱")
        # 用 -p 传字面 profile 而不是写临时文件: 少一个需要清理的文件, 也少一个别人
        # 能在 exec 之前改掉它的窗口.
        return (_SANDBOX_EXEC, "-p", self.profile_for(policy), *argv)

    def self_test(self) -> SelfTestReport:
        """真的去做三次被禁止的操作"""
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

        # 反向确认: 沙箱内的写必须成功. 少了这一步, 一个"什么都拦"的坏 profile 会被
        # 当成完美沙箱.
        if not self._shell_succeeds(policy, f"echo ok > {workspace / 'probe.txt'}"):
            failures.append("沙箱内写入被误拒, profile 不可用")

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
