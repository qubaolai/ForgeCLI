"""ExecutionContext: 一次调用被冻结的执行上下文 (ADR-0004 §2 / §4).

冻结的意义在于消除 TOCTOU: prepare 展开目标用的 cwd, 环境和文件系统视图, 必须与真正
执行时用的是同一份. 上下文一变, 基于旧上下文算出的 ToolPlan 立即作废, 重新走一遍
prepare 与裁决, 而不是"补一下差异".

环境是**已净化**的快照 (ADR-0014 §4.2 的 allowlist 结果), 不是 os.environ. 工具拿到的
就是最终会传给子进程的那一份, 中间没有第二次加工.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePath

from forgecli.application.workspace.filesystem_view import FileSystemView
from forgecli.domain.execution.profile import ExecutionProfile
from forgecli.domain.tool.hashing import digest
from forgecli.domain.tool.plan import ExecutionContextRef, WorkspaceScope
from forgecli.domain.workspace.boundary import is_within

__all__ = ["ExecutionContext"]


@dataclass(frozen=True)
class ExecutionContext:
    """执行上下文快照. workspace_roots[0] 是主工作区根, 其余来自 /add-dir.

    直接持有整个 ExecutionProfile 而不是只存一个 hash: 工具执行需要画像里的 Shell 启动
    参数与受控 PATH, 分开存会让"用来算 hash 的画像"和"实际用来启动进程的参数"可能是
    两份东西 —— 那正是执行画像绑定要防的事.
    """

    cwd: str
    workspace_roots: tuple[str, ...]
    environment: Mapping[str, str]
    filesystem: FileSystemView
    profile: ExecutionProfile
    # workspace_roots 里**只授权了读**的那些 (`/add-dir` 不带 --write).
    # 它们仍然是 root (读得到), 但写入按区外处理.
    readonly_roots: tuple[str, ...] = ()
    toolchain_id: str = "default"

    def __post_init__(self) -> None:
        if not self.workspace_roots:
            raise ValueError("ExecutionContext.workspace_roots 不能为空")

    @property
    def primary_root(self) -> str:
        return self.workspace_roots[0]

    @property
    def execution_profile_hash(self) -> str:
        return self.profile.execution_profile_hash

    @property
    def environment_hash(self) -> str:
        """净化后环境的哈希. 它变了说明 PATH 或注入向量变了, 旧授权必须失效."""
        return digest(dict(self.environment))

    def to_ref(self) -> ExecutionContextRef:
        return ExecutionContextRef(
            cwd=self.cwd,
            environment_hash=self.environment_hash,
            filesystem_view_version=self.filesystem.version,
            toolchain_id=self.toolchain_id,
        )

    def scope_of(self, path: str) -> WorkspaceScope:
        """路径落在哪一档. 按路径段比较, 不做字符串前缀匹配.

        判定用 realpath: 工作区里放一个指向 /etc 的符号链接, 字面路径看着在区内,
        真实目标却在区外.
        """
        target = self.filesystem.facts(path).realpath or path
        if is_within(target, self.primary_root):
            return WorkspaceScope.IN_WORKSPACE
        if any(is_within(target, root) for root in self.workspace_roots[1:]):
            return WorkspaceScope.ADDED_DIR
        return WorkspaceScope.OUTSIDE

    def scope_for_write(self, path: str) -> WorkspaceScope:
        """写入视角的归属. 只读授权目录按区外处理 —— `/add-dir` 不给写就是不给写.

        与 scope_of 分成两个方法, 而不是合并: 只读授权目录"读得到但写不得", 一个方法
        表达不了两件事. 早先 as_roots() 把 READ 与 WRITE 一起摊平成 root, 于是只读
        授权目录在 accept_edits 下直接拿到 WORKSPACE_WRITE 并自动放行.
        """
        scope = self.scope_of(path)
        if scope is WorkspaceScope.ADDED_DIR and self._readonly(path):
            return WorkspaceScope.OUTSIDE
        return scope

    def _readonly(self, path: str) -> bool:
        target = self.filesystem.facts(path).realpath or path
        return any(is_within(target, root) for root in self.readonly_roots)

    def scope_of_all(self, paths: tuple[str, ...]) -> WorkspaceScope:
        """一组路径的整体归属: 取最宽的一档 (有一个在区外, 整次调用就算区外)."""
        return _widest(self.scope_of(path) for path in paths)

    def scope_for_write_all(self, paths: tuple[str, ...]) -> WorkspaceScope:
        """一组**写入**目标的整体归属. 只读授权目录按区外算, 规则同 scope_for_write."""
        return _widest(self.scope_for_write(path) for path in paths)

    def resolve(self, raw: str) -> str:
        """把相对路径按已冻结的 cwd 规范化为绝对路径 (字面归一, 不落盘)."""
        path = PurePath(raw)
        if not path.is_absolute():
            path = PurePath(self.cwd) / path
        parts: list[str] = []
        for part in path.parts:
            if part == ".":
                continue
            if part == ".." and parts and parts[-1] not in ("..", path.anchor):
                parts.pop()
                continue
            parts.append(part)
        return str(PurePath(*parts)) if parts else self.cwd


def _widest(scopes: Iterable[WorkspaceScope]) -> WorkspaceScope:
    """一组归属里最宽的那一档. 有一个在区外, 整次调用就算区外."""
    seen = set(scopes)
    if not seen or seen == {WorkspaceScope.IN_WORKSPACE}:
        return WorkspaceScope.IN_WORKSPACE
    if WorkspaceScope.OUTSIDE in seen:
        return WorkspaceScope.OUTSIDE
    return WorkspaceScope.ADDED_DIR
