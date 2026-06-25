"""项目用例：目录信任、项目查找、工作区目录列表。

ProjectService 只依赖两个存储抽象与领域值对象，不碰 Rich/Typer/TTY；
首启编排 WorkspaceStartup 把「命中绑定 / 非 TTY / 询问→信任 / 拒绝」收敛成纯逻辑，
交互通过 TrustPrompter 端口注入，便于单测用 fake 覆盖。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from forgecli.application.interaction_ports import TrustPrompter
from forgecli.application.project.project import (
    IndexEntry,
    ProjectConfig,
    WorkspaceError,
    generate_project_id,
)
from forgecli.application.project.project_store import (
    ProjectConfigStore,
    ProjectIndexStore,
)
from forgecli.shared.utils import now_iso


def canonical_path(path: Path) -> Path:
    """规范化为绝对路径（展开 ~、resolve 符号链接与 ..）。"""
    return path.expanduser().resolve()


def _is_self_or_ancestor(root: str, target: Path) -> bool:
    """root 是否为 target 自身或其祖先——用路径边界判断，非字符串前缀。"""
    root_path = Path(root)
    return root_path == target or root_path in target.parents


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
            if entry.trusted and _is_self_or_ancestor(entry.root, target)
        ]
        if not matches:
            return None
        best = max(matches, key=lambda entry: len(entry.root))
        return self._configs.load(best.project_id)

    def trust(self, cwd: Path) -> ProjectConfig:
        """信任当前目录：生成 project-id、写 forge.toml 与索引一条。"""
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


@dataclass(frozen=True)
class StartupResult:
    """首启解析结果：project 为 None 表示不进入 REPL。

    reason 取 ``bound`` / ``trusted`` / ``declined`` / ``no_tty``，供上层决定提示语。
    """

    project: ProjectConfig | None
    reason: str


class WorkspaceStartup:
    """裸 forge 进入 REPL 前的目录信任编排（纯逻辑，交互经端口注入）。"""

    def __init__(self, service: ProjectService, prompter: TrustPrompter) -> None:
        self._service = service
        self._prompter = prompter

    def resolve(self, cwd: Path, *, interactive: bool) -> StartupResult:
        existing = self._service.find_trusted(cwd)
        if existing is not None:
            return StartupResult(existing, "bound")
        if not interactive:
            # 非 TTY 无法询问信任：不卡住、不创建配置，交由上层提示并退出。
            return StartupResult(None, "no_tty")
        if self._prompter.confirm(str(canonical_path(cwd))):
            return StartupResult(self._service.trust(cwd), "trusted")
        return StartupResult(None, "declined")
