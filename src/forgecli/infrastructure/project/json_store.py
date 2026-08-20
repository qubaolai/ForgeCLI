"""项目索引与项目配置的 JSON 实现。

两类文件都在用户级 Forge home 下，读写经 infrastructure/json_io（原子写）：
    - ``projects/index.json``：``trusted_roots`` 对象，规范路径作 key。
    - ``projects/<project-id>/forge.json``：单个项目的配置与状态。

行为对齐 application/project/project_store 的两个抽象：load 无文件返回空 / None。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from forgecli.application.project.project_store import (
    ProjectConfigStore,
    ProjectIndexStore,
)
from forgecli.domain.workspace.project import IndexEntry, ProjectConfig
from forgecli.infrastructure.json_io import read_document, write_document

_TRUSTED_ROOTS = "trusted_roots"


class JsonProjectIndexStore(ProjectIndexStore):
    """``projects/index.json`` 的 JSON 实现。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, IndexEntry]:
        roots = read_document(self._path).get(_TRUSTED_ROOTS, {})
        if not isinstance(roots, Mapping):
            return {}
        return {
            str(root): IndexEntry(
                root=str(root),
                project_id=str(body.get("project_id", "")),
                trusted=bool(body.get("trusted", False)),
                created_at=str(body.get("created_at", "")),
                updated_at=str(body.get("updated_at", "")),
            )
            for root, body in roots.items()
            if isinstance(body, Mapping)
        }

    def upsert(self, entry: IndexEntry) -> None:
        document = read_document(self._path)
        roots = document.get(_TRUSTED_ROOTS)
        if not isinstance(roots, dict):
            roots = {}
            document[_TRUSTED_ROOTS] = roots
        roots[entry.root] = {
            "project_id": entry.project_id,
            "trusted": entry.trusted,
            "created_at": entry.created_at,
            "updated_at": entry.updated_at,
        }
        write_document(self._path, document)


class JsonProjectConfigStore(ProjectConfigStore):
    """``projects/<project-id>/forge.json`` 的 JSON 实现。

    构造时只给定 projects 根目录，按 project_id 拼出每个项目文件路径。
    """

    def __init__(self, projects_dir: Path) -> None:
        self._root = projects_dir

    def _file(self, project_id: str) -> Path:
        return self._root / project_id / "forge.json"

    def load(self, project_id: str) -> ProjectConfig | None:
        path = self._file(project_id)
        if not path.exists():
            return None
        data = read_document(path)
        # project_id 或主工作区根缺失: 这份配置修不好 (缺的正是身份本身), 当作没有,
        # 让启动流程重新走一遍首启信任, 而不是造一个半残的 ProjectConfig 出来.
        if not data.get("project_id") or not data.get("primary_workspace_root"):
            return None
        raw_roots = data.get("workspace_roots", [])
        roots = tuple(str(r) for r in raw_roots) if isinstance(raw_roots, list) else ()
        # roots 缺失或不含主根时由 ProjectConfig.__post_init__ 补齐。
        return ProjectConfig(
            project_id=str(data["project_id"]),
            trusted=bool(data.get("trusted", False)),
            primary_workspace_root=str(data["primary_workspace_root"]),
            workspace_roots=roots,
        )

    def save(self, project: ProjectConfig) -> None:
        # 只写项目状态字段；forge.json 里的 logging 等配置偏好由 ConfigService 拥有，
        # 读出后局部修改再写回会原样保留，互不覆盖。
        path = self._file(project.project_id)
        document = read_document(path)
        document["project_id"] = project.project_id
        document["trusted"] = project.trusted
        document["primary_workspace_root"] = project.primary_workspace_root
        document["workspace_roots"] = list(project.workspace_roots)
        write_document(path, document)
