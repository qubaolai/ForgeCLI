"""进程内的当前项目持有者。

项目本身的值对象（ProjectConfig / IndexEntry / WorkspaceError / generate_project_id）
住在 domain.workspace.project；这里只留 ProjectContext —— 它是**可变**的进程内持有者，
REPL 装配时建立一次，/add-dir 写入新的 ProjectConfig，/status 读取它。

用一个薄持有者而非把项目塞进会话态，是为了不让会话态耦合项目存储。
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.workspace.project import ProjectConfig

__all__ = ["ProjectContext"]


@dataclass
class ProjectContext:
    """进程内可变的当前项目持有者。

    REPL 装配时建立一次；/add-dir 写入新的 ProjectConfig，/status 读取它。
    用一个薄持有者而非把项目塞进会话态，是为了不让会话态耦合项目存储。
    """

    project: ProjectConfig
