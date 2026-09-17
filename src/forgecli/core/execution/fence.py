"""围栏策略: 子进程能写哪里, 不能读哪里, 能不能联网 (ADR-0030 决策 1; ADR-0055 决策 7).

围栏是**运行期**强制的, 不是执行前推导的. 这里只有边界, 没有任何命令正文知识: `rm -rf
$DIR` 和 `ls` 在这个模块眼里没有区别. ADR-0055 之后它由沙箱设置 (`core/sandbox.py`)
与工作目录编译, 不再由模式编译 —— 模式说"什么时候问人", 沙箱说"子进程能碰什么".

两条边界的形态不对称, 这是实测出来的 (ADR-0030 实测记录第二节):

- **写**: allowlist. `(deny file-write*)` + 逐项 allow 工作区, 实测通过.
- **读**: 只能 denylist. Seatbelt 下 `(deny default)` 与 `(deny file-read*)` + 系统
  allowlist 两种形态都会让子进程 SIGABRT, 连 `/bin/sh` 都起不来. 所以受保护路径仍然
  要逐条列举, 漏一条就是凭证暴露.
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.core.sandbox import SandboxSettings
from forgecli.core.tool.hashing import digest

__all__ = ["FencePolicy", "fence_for"]


@dataclass(frozen=True)
class FencePolicy:
    """一次执行的边界. 进 ExecutionProfile 的 hash, 变了就使旧授权失效."""

    writable_roots: tuple[str, ...] = ()
    denied_read_paths: tuple[str, ...] = ()
    network_allowed: bool = False
    # 实例私有临时目录. 单独一个字段而**不是**并进 writable_roots: 并进去之后
    # `read_only` 会永远是 False, 而 plan 档的全部意义就是那个 True.
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
    instance_temp_root: str = "",
) -> FencePolicy:
    """把沙箱设置与工作目录编译成围栏策略.

    `readonly_roots` 是只读的额外目录: 读得到但写不进, 所以进工作区根却不进可写集合.
    `sandbox.allow_write` 是设置里额外放开的可写路径 (对应
    sandbox.filesystem.allowWrite).
    """
    writable = tuple(
        root for root in workspace_roots if root not in set(readonly_roots)
    )
    extra = tuple(path for path in sandbox.allow_write if path not in writable)
    return FencePolicy(
        writable_roots=writable + extra,
        denied_read_paths=tuple(sorted(dict.fromkeys(protected_paths))),
        network_allowed=sandbox.network_allowed,
        # 实例私有临时目录始终可写, 否则连 mktemp 都用不了, 而那会让绝大多数真实命令
        # 失败. 它不算"工作区可写", 所以不进 writable_roots.
        instance_temp_root=instance_temp_root,
    )
