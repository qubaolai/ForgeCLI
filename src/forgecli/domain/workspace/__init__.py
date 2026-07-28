"""工作区的领域词汇: 项目配置, 索引条目, 目录合法性错误。"""

from forgecli.domain.workspace.boundary import is_within
from forgecli.domain.workspace.project import (
    IndexEntry,
    ProjectConfig,
    WorkspaceError,
    generate_project_id,
)

__all__ = [
    "IndexEntry",
    "ProjectConfig",
    "WorkspaceError",
    "generate_project_id",
    "is_within",
]
