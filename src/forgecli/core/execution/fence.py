"""围栏策略: 子进程能写哪里, 不能读哪里, 网络怎么出去 (ADR-0030 决策 1; ADR-0055 决策 7)

围栏是**运行期**强制的, 不是执行前推导的. 这里只有边界, 没有任何命令正文知识: `rm -rf
$DIR` 和 `ls` 在这个模块眼里没有区别. 它由沙箱设置 (`core/sandbox.py`), 工作目录与
权限规则里的路径编译, 不由模式编译 —— 模式说"什么时候问人", 沙箱说"子进程能碰什么".

四条边界, 形态照 sandbox-runtime:

- **写**: allowlist, 再叠一层 deny (deny 优先). `(deny file-write*)` + 逐项 allow 工作
  区, 再 deny 掉 `.git/hooks`, `~/.forge` 这类即使在可写区里也不许碰的路径.
- **读**: 只能 denylist, 再叠一层 allow (allow 优先). Seatbelt 下 `(deny default)` 与
  `(deny file-read*)` + 系统 allowlist 两种形态都会让子进程 SIGABRT (ADR-0030 实测记录
  第二节), 所以受保护路径仍然要逐条列举, 漏一条就是凭证暴露.
- **网络**: 全拦, 只放一个本机代理端口; 代理按域名放行 (`NetworkFence`). 另可放开
  unix socket 与本机端口.
- **临时目录**: 实例私有, 始终可写, 子进程的 TMPDIR 指向它. 单独一个字段而**不是**并进
  writable_roots: 并进去之后 `read_only` 会永远是 False, 而 plan 档的全部意义就是那个
  True.
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.core.sandbox import SandboxSettings
from forgecli.core.tool.hashing import digest

__all__ = ["FencePolicy", "NetworkFence", "fence_for"]


@dataclass(frozen=True)
class NetworkFence:
    """网络边界. `proxy_port` 为 None 且 `unrestricted` 为假 = 一个字节都出不去."""

    # 本机代理监听的端口 (macOS: 围栏只放行到它的连接; Linux: 见 bubblewrap 的桥).
    proxy_port: int | None = None
    # 代理监听的 unix socket (Linux 桥用: bind 进围栏, 围栏内 socat 转成 TCP).
    proxy_socket: str = ""
    # allowedDomains 含 `*`: 围栏直接放开网络, 不经代理.
    unrestricted: bool = False
    allow_unix_sockets: tuple[str, ...] = ()
    allow_all_unix_sockets: bool = False
    allow_local_binding: bool = False

    @property
    def proxied(self) -> bool:
        return not self.unrestricted and (
            self.proxy_port is not None or bool(self.proxy_socket)
        )


@dataclass(frozen=True)
class FencePolicy:
    """一次执行的边界. 进 ExecutionProfile 的 hash, 变了就使旧授权失效."""

    writable_roots: tuple[str, ...] = ()
    # 可写区里再挖掉的路径: deny 优先于 allow (sandbox-runtime 的 denyWrite).
    denied_write_paths: tuple[str, ...] = ()
    denied_read_paths: tuple[str, ...] = ()
    # denied_read_paths 里再放开的路径: allow 优先于 deny (sandbox-runtime 的
    # allowRead).
    allowed_read_paths: tuple[str, ...] = ()
    network: NetworkFence = NetworkFence()
    instance_temp_root: str = ""

    @property
    def policy_hash(self) -> str:
        return digest(self)

    @property
    def read_only(self) -> bool:
        """没有任何工作区根可写 —— plan 档就是这个形态."""
        return not self.writable_roots

    @property
    def all_writable(self) -> tuple[str, ...]:
        """生成 profile 时真正要放行写入的全部路径."""
        if not self.instance_temp_root:
            return self.writable_roots
        return (*self.writable_roots, self.instance_temp_root)


def fence_for(
    sandbox: SandboxSettings,
    *,
    workspace_roots: tuple[str, ...],
    readonly_roots: tuple[str, ...] = (),
    protected_paths: tuple[str, ...] = (),
    extra_writable: tuple[str, ...] = (),
    denied_write: tuple[str, ...] = (),
    denied_read: tuple[str, ...] = (),
    allowed_read: tuple[str, ...] = (),
    instance_temp_root: str = "",
    network: NetworkFence | None = None,
) -> FencePolicy:
    """把沙箱设置, 工作目录与规则里的路径编译成围栏策略.

    `readonly_roots` 是只读的额外目录: 读得到但写不进, 所以进工作区根却不进可写集合.
    `extra_writable` / `denied_write` / `denied_read` / `allowed_read` 是调用方从权限
    规则 (`Edit(...)` / `Read(...)`) 与沙箱设置 (`filesystem.*`) 解出来的绝对路径
    (`security/sandbox_paths.py`) —— 规则的路径写法 (`//abs`, `~/`, 相对设置文件) 归
    security 层解, 这里只收绝对路径, 所以 `sandbox.allow_write` 那几个字段不在这里读.
    """
    excluded = set(readonly_roots)
    writable = tuple(root for root in workspace_roots if root not in excluded)
    extra = tuple(
        dict.fromkeys(path for path in extra_writable if path not in writable)
    )
    denied_reads = dict.fromkeys((*protected_paths, *denied_read))
    return FencePolicy(
        writable_roots=writable + extra,
        denied_write_paths=tuple(dict.fromkeys(denied_write)),
        denied_read_paths=tuple(sorted(denied_reads)),
        allowed_read_paths=tuple(dict.fromkeys(allowed_read)),
        network=network
        if network is not None
        else NetworkFence(
            unrestricted=sandbox.network_unrestricted,
            allow_unix_sockets=sandbox.allow_unix_sockets,
            allow_all_unix_sockets=sandbox.allow_all_unix_sockets,
            allow_local_binding=sandbox.allow_local_binding,
        ),
        # 实例私有临时目录始终可写, 否则连 mktemp 都用不了, 而那会让绝大多数真实命令
        # 失败. 它不算"工作区可写", 所以不进 writable_roots.
        instance_temp_root=instance_temp_root,
    )
