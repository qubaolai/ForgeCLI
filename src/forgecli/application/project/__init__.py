"""项目上下文：目录信任、用户级项目存储、工作区目录列表（ADR-0008）。"""

from forgecli.application.project.project import ProjectContext
from forgecli.application.project.project_service import (
    ProjectService,
    StartupResult,
    WorkspaceStartup,
    canonical_path,
)
from forgecli.application.project.project_store import (
    ProjectConfigStore,
    ProjectIndexStore,
)
from forgecli.domain.workspace.project import (
    IndexEntry,
    ProjectConfig,
    WorkspaceError,
    generate_project_id,
)

__all__ = [
    "IndexEntry",
    "ProjectConfig",
    "ProjectContext",
    "ProjectConfigStore",
    "ProjectIndexStore",
    "ProjectService",
    "StartupResult",
    "WorkspaceError",
    "WorkspaceStartup",
    "canonical_path",
    "generate_project_id",
]
