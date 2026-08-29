"""项目用例：目录信任、项目查找、工作区目录列表。

ProjectService 只依赖两个存储抽象与领域值对象，不碰任何界面细节：信任与否是
Web 控制面问出来的，问法不进这一层。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from forgecli.application.project.project_store import (
    ProjectConfigStore,
    ProjectIndexStore,
)
from forgecli.domain.workspace.boundary import is_within
from forgecli.domain.workspace.project import (
    IndexEntry,
    ProjectConfig,
    WorkspaceError,
    generate_project_id,
)
from forgecli.shared.utils import now_iso


def canonical_path(path: Path) -> Path:
    """规范化为绝对路径（展开 ~、resolve 符号链接与 ..）。

    **不进 domain**: resolve() 会读文件系统解 symlink, 是 IO。纯粹的边界比较在
    domain.workspace.boundary.is_within, 那里只按路径段比, 不碰磁盘。
    """
    return path.expanduser().resolve()


class ProjectService:
    """目录信任与工作区列表的读写用例。"""

    def __init__(
        self,
        index_store: ProjectIndexStore,
        config_store: ProjectConfigStore,
        clock: Callable[[], str] = now_iso,
    ) -> None:
        self._index = index_store
        self._configs = config_store
        self._clock = clock

    def find_trusted(self, cwd: Path) -> ProjectConfig | None:
        """按已信任项目根匹配当前目录；命中多个取根路径最长者。

        只用 ``primary_workspace_root``（即索引 key）参与身份匹配；额外
        workspace_roots 仅表示可操作目录，不改变“进目录即进项目”的语义。
        """
        target = canonical_path(cwd)
        matches = [
            entry
            for entry in self._index.load().values()
            if entry.trusted and is_within(target, entry.root)
        ]
        if not matches:
            return None
        best = max(matches, key=lambda entry: len(entry.root))
        return self._configs.load(best.project_id)

    def list_trusted(self) -> tuple[ProjectConfig, ...]:
        """列出所有仍可读取的已信任项目，供 Web 项目中心使用。

        索引是项目身份与信任状态的真相源；损坏或缺失的项目配置被忽略，避免项目中心
        展示一个点击后无法激活的半残项目。排序使用主工作区路径，保证跨进程稳定。
        """
        projects: list[ProjectConfig] = []
        for entry in self._index.load().values():
            if not entry.trusted:
                continue
            project = self._configs.load(entry.project_id)
            if project is not None and project.trusted:
                projects.append(project)
        projects.sort(key=lambda item: item.primary_workspace_root.casefold())
        return tuple(projects)

    def get(self, project_id: str) -> ProjectConfig | None:
        """按 id 读取一个已信任项目；未知或已撤销信任时返回 ``None``。"""
        project = self._configs.load(project_id)
        if project is None or not project.trusted:
            return None
        indexed = self._index.load().get(project.primary_workspace_root)
        if indexed is None or not indexed.trusted or indexed.project_id != project_id:
            return None
        return project

    def trust(self, cwd: Path) -> ProjectConfig:
        """信任当前目录：生成 project-id、写 forge.json 与索引一条。"""
        root = canonical_path(cwd)
        root_str = str(root)
        project = ProjectConfig(
            project_id=generate_project_id(root),
            trusted=True,
            primary_workspace_root=root_str,
            workspace_roots=(root_str,),
        )
        self._configs.save(project)
        now = self._clock()
        self._index.upsert(
            IndexEntry(
                root=root_str,
                project_id=project.project_id,
                trusted=True,
                created_at=now,
                updated_at=now,
            )
        )
        return project

    def normalize_workspace_dir(self, raw: str, base: Path) -> Path:
        """把用户输入归一为绝对规范路径并校验存在且是目录。"""
        text = raw.strip()
        if not text:
            raise WorkspaceError("目录路径不能为空。")
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = base / candidate
        resolved = candidate.resolve()
        if not resolved.exists():
            raise WorkspaceError(f"目录不存在：{resolved}")
        if not resolved.is_dir():
            raise WorkspaceError(f"不是目录：{resolved}")
        return resolved

    def add_workspace_dir(self, project: ProjectConfig, path: Path) -> ProjectConfig:
        """把已规范化目录加入 workspace_roots；已存在则原样返回（无感跳过）。"""
        value = str(path)
        if value in project.workspace_roots:
            return project
        updated = replace(project, workspace_roots=(*project.workspace_roots, value))
        self._configs.save(updated)
        return updated

    def remove_workspace_dir(self, project: ProjectConfig, path: Path) -> ProjectConfig:
        """移除额外工作区目录；主工作区根是项目身份，不允许移除。"""
        value = str(path)
        if value == project.primary_workspace_root:
            raise WorkspaceError("不能移除项目的主工作区目录。")
        if value not in project.workspace_roots:
            return project
        updated = replace(
            project,
            workspace_roots=tuple(
                root for root in project.workspace_roots if root != value
            ),
        )
        self._configs.save(updated)
        return updated
