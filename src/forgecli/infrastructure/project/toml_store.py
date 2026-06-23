"""项目索引与项目配置的 tomlkit 实现。

两类文件都在用户级 Forge home 下，读写经 infrastructure/toml_io（round-trip + 原子写）：
    - index.toml：trusted_roots 表，规范路径作 key（tomlkit 自动加引号）。
    - <project-id>/project.toml：单个项目的配置与状态。

行为对齐 application/project/project_store 的两个抽象：load 无文件返回空 / None。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import tomlkit

from forgecli.application.project.project import IndexEntry, ProjectConfig
from forgecli.application.project.project_store import (
    ProjectConfigStore,
    ProjectIndexStore,
)
from forgecli.infrastructure.toml_io import read_document, write_document

_TRUSTED_ROOTS = "trusted_roots"


class TomlProjectIndexStore(ProjectIndexStore):
    """``projects/index.toml`` 的 tomlkit 实现。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, IndexEntry]:
        roots = read_document(self._path).unwrap().get(_TRUSTED_ROOTS, {})
        if not isinstance(roots, Mapping):
            return {}
        result: dict[str, IndexEntry] = {}
        for root, body in roots.items():
            if not isinstance(body, Mapping):
                continue
            result[root] = IndexEntry(
                root=root,
                project_id=str(body.get("project_id", "")),
                trusted=bool(body.get("trusted", False)),
                created_at=str(body.get("created_at", "")),
                updated_at=str(body.get("updated_at", "")),
            )
        return result

    def upsert(self, entry: IndexEntry) -> None:
        doc = read_document(self._path)
        roots = doc.get(_TRUSTED_ROOTS)
        if not isinstance(roots, tomlkit.items.Table):
            roots = tomlkit.table()
            doc[_TRUSTED_ROOTS] = roots
        body = tomlkit.table()
        body["project_id"] = entry.project_id
        body["trusted"] = entry.trusted
        body["created_at"] = entry.created_at
        body["updated_at"] = entry.updated_at
        roots[entry.root] = body
        write_document(self._path, doc)


class TomlProjectConfigStore(ProjectConfigStore):
    """``projects/<project-id>/project.toml`` 的 tomlkit 实现。

    构造时只给定 projects 根目录，按 project_id 拼出每个项目文件路径。
    """

    def __init__(self, projects_dir: Path) -> None:
        self._root = projects_dir

    def _file(self, project_id: str) -> Path:
        return self._root / project_id / "project.toml"

    def load(self, project_id: str) -> ProjectConfig | None:
        path = self._file(project_id)
        if not path.exists():
            return None
        data = read_document(path).unwrap()
        if not data.get("project_id"):
            return None
        raw_roots = data.get("workspace_roots", [])
        roots = tuple(str(r) for r in raw_roots) if isinstance(raw_roots, list) else ()
        logging = data.get("logging", {})
        level = logging.get("level", "info") if isinstance(logging, Mapping) else "info"
        return ProjectConfig(
            project_id=str(data["project_id"]),
            trusted=bool(data.get("trusted", False)),
            primary_workspace_root=str(data.get("primary_workspace_root", "")),
            workspace_roots=roots,
            log_level=str(level),
        )

    def save(self, project: ProjectConfig) -> None:
        path = self._file(project.project_id)
        doc = read_document(path)
        doc["project_id"] = project.project_id
        doc["trusted"] = project.trusted
        doc["primary_workspace_root"] = project.primary_workspace_root
        doc["workspace_roots"] = list(project.workspace_roots)
        logging = doc.get("logging")
        if not isinstance(logging, tomlkit.items.Table):
            logging = tomlkit.table()
            doc["logging"] = logging
        logging["level"] = project.log_level
        write_document(path, doc)
