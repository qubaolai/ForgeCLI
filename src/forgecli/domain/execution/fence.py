"""围栏策略: 子进程能否自动启动, 能写哪里, 不能读哪里, 能不能联网 (ADR-0030 决策 1 / 4).

围栏是**运行期**强制的, 不是执行前推导的. 所以这里只有模式边界, 没有任何命令正文
知识 ---- `rm -rf $DIR` 和 `ls` 在这个模块眼里没有区别: accept_edits 下都先问人,
auto 下都交给内核在系统调用那一刻守住路径与网络边界.

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
    # 是否允许裁决层在不询问人的情况下启动通用 Shell. 它不改变操作系统围栏本身,
    # 只表达模式的自主性边界: accept_edits 可以自动使用专用文件工具, 但 shell_run
    # 必须先问; auto / full_access 才能在真围栏内自动启动 Shell.
    #
    # 仍放在围栏策略里, 是为了和 writable_roots / unrestricted 一样进入 policy_hash:
    # 切模式后, 旧授权不能沿用到自主性更高的新边界.
    automatic_shell: bool = False
    # full_access: 围栏就是全部边界, 裁决侧除 Hard Deny 外不再另设闸 (ADR-0030 决策 4
    # 的 2026-08-28 修订). 它进 policy_hash, 所以切模式会让旧授权失效.
    #
    # 放在围栏上而不是让裁决侧自己看 SessionMode: "把模式编译成边界"就是这个模块的
    # 职责, 多一个地方解释模式, 就多一处会和它不一致.
    unrestricted: bool = False
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
    "这次调用请求了哪些 Capability", 而那要靠分析器从命令串里推导; 这里直接表达
    "这个模式能否自动启动 Shell, 允许触达哪些路径", 与命令内容无关.

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
        automatic_shell=mode in (SessionMode.AUTO, SessionMode.FULL_ACCESS),
        unrestricted=mode is SessionMode.FULL_ACCESS,
        # 实例私有临时目录始终可写, 否则连 mktemp 都用不了, 而那会让绝大多数真实命令
        # 失败. 它不算"工作区可写", 所以不进 writable_roots.
        instance_temp_root=instance_temp_root,
    )
