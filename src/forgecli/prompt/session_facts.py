"""会话段的输入: 整个会话内不该变的事实 (ADR-0056 决策 2 / 决策 7).

它不是 ExecutionProfile. profile 里带着 trusted_path, controlled_environment 与
protected_roots_hash —— 这些绝不能进提示词. 投影发生在组合根 (from_profile), 提示词只能
看见已经脱敏的这一份, 于是"渲染全部字段"成为安全的默认行为.

每轮都变的东西 (模式, 日期变化, 计划, 待办) 不在这里: 这里的字段变了, 整个前缀作废,
那必须是罕见的事.
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.core.execution.profile import ExecutionProfile

__all__ = ["GitSnapshot", "SessionFacts"]


@dataclass(frozen=True)
class GitSnapshot:
    """会话开始时的 git 状态. 之后不更新, 提示词里明说这是快照."""

    branch: str
    main_branch: str
    user_name: str
    status: str
    recent_commits: str


@dataclass(frozen=True)
class SessionFacts:
    platform: str
    shell_kind: str
    working_directory: str
    workspace_roots: tuple[str, ...]
    git_repository: bool
    sandboxed: bool
    model: str
    # ISO 日期. 跨天由通知告诉模型 (决策 7), 这里的值不变.
    start_date: str
    git: GitSnapshot | None = None

    def __post_init__(self) -> None:
        if not self.workspace_roots:
            raise ValueError("SessionFacts.workspace_roots 不能为空")

    @classmethod
    def from_profile(
        cls,
        profile: ExecutionProfile,
        *,
        working_directory: str,
        workspace_roots: tuple[str, ...],
        git_repository: bool,
        sandboxed: bool,
        model: str,
        start_date: str,
        git: GitSnapshot | None = None,
    ) -> SessionFacts:
        """从执行画像投影. 只取两个字段, 其余一律不带出来."""
        return cls(
            platform=profile.platform,
            shell_kind=profile.shell_launch.kind,
            working_directory=working_directory,
            workspace_roots=workspace_roots,
            git_repository=git_repository,
            sandboxed=sandboxed,
            model=model,
            start_date=start_date,
            git=git,
        )
