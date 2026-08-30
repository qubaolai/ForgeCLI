"""目录授权: `/add-dir` 的读写分级 (ADR-0014 §7).

沙箱本次不实现, 但这层与沙箱无关: 规则引擎要靠它算 `workspace_scope`, 决定一次读写算
"区内", "已授权目录"还是"区外". 没有它, `/add-dir` 加进来的目录与随便一个系统目录在
裁决层面没有区别.

三条硬性语义:

- 默认只读. 读写必须显式 `--write` 并单独确认.
- `ProtectedPathPolicy` 优先: 凭证, 系统目录和 Forge 自身数据目录不能通过授权打开.
- 只能由用户发起. LLM 可以建议用户执行, 但不能自己调用来扩大访问范围.

策略变化会递增 `policy_version`, 使旧授权与旧审批失效.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from forgecli.domain.security.protected_paths import ProtectedPathPolicy
from forgecli.domain.tool.hashing import digest
from forgecli.domain.workspace.boundary import is_within

__all__ = ["DirectoryGrant", "GrantAccess", "GrantError", "WorkspaceGrants"]


class GrantAccess(Enum):
    READ = "read"
    WRITE = "write"


@dataclass(frozen=True)
class DirectoryGrant:
    path: str
    access: GrantAccess
    granted_at: str


class GrantError(Exception):
    """授权被拒. message 可直接展示给用户."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class WorkspaceGrants:
    """当前会话的目录授权集合."""

    def __init__(
        self,
        protected_paths: ProtectedPathPolicy,
        *,
        grants: tuple[DirectoryGrant, ...] = (),
        version: int = 1,
    ) -> None:
        self._protected = protected_paths
        self._grants: list[DirectoryGrant] = list(grants)
        self._version = version

    @property
    def policy_version(self) -> int:
        """每次授权变更递增. 进执行画像, 变了就让旧授权失效."""
        return self._version

    @property
    def grants(self) -> tuple[DirectoryGrant, ...]:
        return tuple(self._grants)

    @property
    def grants_hash(self) -> str:
        return digest(
            [
                (grant.path, grant.access.value)
                for grant in sorted(self._grants, key=lambda item: item.path)
            ]
        )

    def grant(
        self, realpath: str, access: GrantAccess, *, granted_at: str
    ) -> DirectoryGrant:
        """新增或升级一个目录授权.

        realpath 必须是**已解析**的最终目标: 用符号链接指向 /etc 再 /add-dir, 字面路径
        看起来人畜无害.
        """
        root = self._protected.classify(realpath)
        if root is not None:
            raise GrantError(f"受保护路径不能授权 ({root.category.value}): {realpath}")
        existing = self._find(realpath)
        if existing is not None:
            if existing.access is access:
                return existing
            self._grants.remove(existing)
        grant = DirectoryGrant(path=realpath, access=access, granted_at=granted_at)
        self._grants.append(grant)
        self._version += 1
        return grant

    def revoke(self, realpath: str) -> bool:
        existing = self._find(realpath)
        if existing is None:
            return False
        self._grants.remove(existing)
        self._version += 1
        return True

    def access_for(self, realpath: str) -> GrantAccess | None:
        """该路径落在哪个授权目录下. 命中多个取最具体的那个."""
        matches = [grant for grant in self._grants if is_within(realpath, grant.path)]
        if not matches:
            return None
        best = max(matches, key=lambda grant: len(grant.path))
        return best.access

    def as_roots(self) -> tuple[str, ...]:
        """交给 ExecutionContext 的附加工作区根 (顺序稳定, 便于哈希).

        读写授权都在里面: 两者都**可读**. 写入权限由 readonly_roots 区分 —— 只靠这一个
        方法传递授权会把 READ 与 WRITE 摊平成同一件事.
        """
        return tuple(sorted(grant.path for grant in self._grants))

    def readonly_roots(self) -> tuple[str, ...]:
        """只授权了读的那些目录. ExecutionContext 用它把写入判成区外."""
        return tuple(
            sorted(
                grant.path
                for grant in self._grants
                if grant.access is not GrantAccess.WRITE
            )
        )

    def _find(self, realpath: str) -> DirectoryGrant | None:
        return next((grant for grant in self._grants if grant.path == realpath), None)

    def snapshot(self) -> WorkspaceGrants:
        """冻结一份副本: 执行期间用户改授权不影响正在跑的这一次."""
        return WorkspaceGrants(
            self._protected,
            grants=tuple(replace(grant) for grant in self._grants),
            version=self._version,
        )
