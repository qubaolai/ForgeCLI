"""工作区的只读视图与共享执行上下文.

这里只放**工具与安全模块都要用**的东西: 文件系统视图与冻结的执行上下文. 两侧不能互相
import, 所以这些共享抽象必须住在双方之外的中立位置.

工作区身份, 信任和目录列表属于 application/project; 目录的读写授权属于安全模块.
"""

from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.application.workspace.filesystem_view import (
    FileSystemView,
    PathFacts,
    PathKind,
)

__all__ = ["ExecutionContext", "FileSystemView", "PathFacts", "PathKind"]
