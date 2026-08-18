"""RuntimeFacts: 可以安全渲染进提示词的运行事实 (ADR-0018 §4.3).

它不是 ExecutionProfile. profile 里带着 trusted_path, environment_allowlist 与
protected_roots_hash —— 这些绝不能进提示词. 让 builder 直接收 profile, 等于把"哪些
字段不许渲染"这条规则散落进渲染代码, 而漏掉一条的后果是把受控 PATH 原样发给供应商.

投影发生在组合根 (from_profile), builder 只能看见已经脱敏的这一份. 于是"渲染全部字段"
成为安全的默认行为, 而不是每次加字段都要重新判断一遍.

刻意不带 catalog_snapshot_hash: 模型拿它做不了任何事, 它只进调用 trace (ADR-0018 §4.3).
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.execution.profile import ExecutionProfile, IsolationLevel

__all__ = ["RuntimeFacts"]

_ISOLATION_SUMMARY: dict[IsolationLevel, str] = {
    IsolationLevel.STRONG_SANDBOX: "命令在沙箱中运行, 触达宿主资源受限",
    IsolationLevel.PARTIAL_SANDBOX: "命令部分隔离, 仍能触达部分宿主资源",
    IsolationLevel.NO_SANDBOX: "命令直接在宿主上运行, 没有额外隔离",
}


@dataclass(frozen=True)
class RuntimeFacts:
    """一轮开始时的运行环境事实. 全部字段都可以直接渲染."""

    platform: str
    shell_kind: str
    isolation_level: IsolationLevel
    working_directory: str
    workspace_roots: tuple[str, ...]
    git_repository: bool

    def __post_init__(self) -> None:
        if not self.workspace_roots:
            raise ValueError("RuntimeFacts.workspace_roots 不能为空")

    @property
    def isolation_summary(self) -> str:
        return _ISOLATION_SUMMARY[self.isolation_level]

    @classmethod
    def from_profile(
        cls,
        profile: ExecutionProfile,
        *,
        working_directory: str,
        workspace_roots: tuple[str, ...],
        git_repository: bool,
    ) -> RuntimeFacts:
        """从执行画像投影. 只取三个字段, 其余一律不带出来."""
        return cls(
            platform=profile.platform,
            shell_kind=profile.shell_launch.kind,
            isolation_level=profile.isolation_level,
            working_directory=working_directory,
            workspace_roots=workspace_roots,
            git_repository=git_repository,
        )
