"""只读文件系统视图: 工具与安全模块共用的唯一读盘入口.

为什么需要一层抽象:

- ADR-0004 §2 要求 prepare 只读, 且必须使用 ExecutionContext 中**已经冻结**的文件系统
  视图 —— 冻结这件事需要一个可以带版本号的对象, 裸 pathlib 做不到.
- 工具 (tools) 与安全模块 (security) 都要读盘, 但两者不能互相 import. 视图放在双方之外
  的中立位置, 是唯一能同时满足"都能用"和"互不依赖"的位置.
- 测试要能注入内存实现: 路径规范化, 受保护路径和可执行文件身份的用例不该依赖真实磁盘
  上恰好存在什么.

视图只读. 真实写入走 FileTool 与恢复层, 不从这里走.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

__all__ = ["FileSystemView", "PathFacts", "PathKind"]


class PathKind(Enum):
    MISSING = "missing"
    FILE = "file"
    DIRECTORY = "directory"
    OTHER = "other"  # 设备, FIFO, socket 等; 一律不当作普通文件处理


@dataclass(frozen=True)
class PathFacts:
    """一个路径的身份事实.

    path 与 realpath 分开: 受保护路径判定必须看 realpath (符号链接, junction 和
    macOS /private 别名都会让字符串前缀匹配失效), 而审批展示要让用户看到他原本写的
    那个 path.

    file_identity 是"同一个对象"的判据 (POSIX 下 dev:ino, Windows 下卷序列号 + File
    ID). 执行前后 identity 变了就说明对象被替换过, 旧授权与旧 preimage 都作废.
    """

    path: str
    realpath: str
    kind: PathKind
    size: int = 0
    mtime_ns: int = 0
    file_identity: str = ""
    mode: int = 0
    is_symlink: bool = False
    link_target: str | None = None

    @property
    def exists(self) -> bool:
        return self.kind is not PathKind.MISSING

    @property
    def is_regular_file(self) -> bool:
        return self.kind is PathKind.FILE


class FileSystemView(ABC):
    """带版本的只读视图. version 变化即所有基于旧视图的展开与裁决作废."""

    @property
    @abstractmethod
    def version(self) -> str:
        """视图版本, 进 ExecutionContextRef.filesystem_view_version."""

    @abstractmethod
    def facts(self, path: str) -> PathFacts:
        """读取路径身份事实. 路径不存在返回 kind=MISSING 而不是抛错."""

    @abstractmethod
    def read_text(self, path: str, *, max_bytes: int) -> str:
        """读文本. 超过 max_bytes 时截断到上限, 不抛错 (调用方按需判断截断)."""

    @abstractmethod
    def read_bytes(self, path: str, *, max_bytes: int) -> bytes:
        """读原始字节 (可执行文件哈希用)."""

    @abstractmethod
    def list_dir(self, path: str) -> tuple[str, ...]:
        """列目录下的直接子项名 (不递归). 不是目录或不可读时返回空."""

    @abstractmethod
    def expand_glob(self, pattern: str, *, root: str) -> tuple[str, ...]:
        """按已冻结的视图展开一个 glob, 返回排序后的绝对路径."""
