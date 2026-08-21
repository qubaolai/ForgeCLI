"""围栏策略: 子进程能写哪里, 不能读哪里, 能不能联网 (ADR-0030 决策 1 / 4).

围栏是**运行期**强制的, 不是执行前推导的. 所以这里只有边界, 没有任何命令知识 ----
`rm -rf $DIR` 和 `ls` 在这个模块眼里没有区别, 它们的差别由内核在系统调用那一刻说了算.

两条边界的形态不对称, 这是实测出来的 (ADR-0030 实测记录第二节):

- **写**: allowlist. `(deny file-write*)` + 逐项 allow 工作区, 实测通过.
- **读**: 只能 denylist. Seatbelt 下 `(deny default)` 与 `(deny file-read*)` + 系统
  allowlist 两种形态都会让子进程 SIGABRT, 连 `/bin/sh` 都起不来. 所以受保护路径仍然
  要逐条列举, 漏一条就是凭证暴露 —— 这条风险本 ADR 没能消除, 只在 Linux 上由
  bubblewrap 的 mount namespace 解决.
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.intents import SessionMode
from forgecli.domain.tool.hashing import digest

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
    mode: SessionMode,
    *,
    workspace_roots: tuple[str, ...],
    readonly_roots: tuple[str, ...] = (),
    protected_paths: tuple[str, ...] = (),
    instance_temp_root: str = "",
) -> FencePolicy:
    """把模式编译成围栏策略 (ADR-0030 决策 4).

    这个函数顶替了 `domain/security/modes.py` 的能力预算表. 差别在判据: 预算表问的是
    "这次调用请求了哪些 Capability", 而那要靠分析器从命令串里推导; 这里问的是
    "这个模式允许触达哪些路径", 与命令内容无关.

    `readonly_roots` 来自不带 `--write` 的 `/add-dir`: 它们读得到但写不进, 所以进
    工作区根却不进可写集合.
    """
    writable: tuple[str, ...] = ()
    if mode is not SessionMode.PLAN:
        writable = tuple(
            root for root in workspace_roots if root not in set(readonly_roots)
        )
    return FencePolicy(
        writable_roots=writable,
        denied_read_paths=tuple(sorted(dict.fromkeys(protected_paths))),
        network_allowed=mode is SessionMode.FULL_ACCESS,
        # 实例私有临时目录始终可写, 否则连 mktemp 都用不了, 而那会让绝大多数真实命令
        # 失败. 它不算"工作区可写", 所以不进 writable_roots.
        instance_temp_root=instance_temp_root,
    )
