"""只读文件系统视图: 工具与安全模块共用的唯一读盘入口.

为什么需要一层抽象:

- ADR-0004 §2 要求 prepare 只读, 且必须使用 ExecutionContext 中同一个文件系统观察入口。
  version 标识这次上下文实例；它不是操作系统快照，真正的 TOCTOU 保护由 ToolPlan 中的
  文件状态绑定和执行前复核完成。
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

    file_identity 是最终会被读取的对象判据 (POSIX 下 dev:ino, Windows 下卷序列号 + File
    ID)。链接本身由 is_symlink/link_target 表达；realpath 或 identity 变化时，
    旧授权与旧 preimage 都作废。
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
    """带实例版本的只读观察入口；不承诺冻结整个操作系统文件系统。"""

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
    def is_ignored(self, path: str, *, root: str) -> bool:
        """这条路径是不是被忽略的生成物. 判断交给权威来源, 不由调用方列名单."""

    @abstractmethod
    def read_text_if_text(self, path: str, *, max_bytes: int) -> str | None:
        """文本内容; 判定为二进制时返回 None.

        与 `read_text` 分开而不是让它自己返回空串: "空文件"和"二进制文件"对调用方是两件
        事. `search_text` 靠这个区分把 `.class` / `.jar` 挡在扫描之外 —— 它有 2000 个
        文件与 200 条命中的额度, 二进制解码出来的替换字符照样会产生命中, 于是额度被喂给
        噪音, 而"这个词不在代码里"这个结论建立在没扫到源码上.
        """

    @abstractmethod
    def read_bytes(self, path: str, *, max_bytes: int) -> bytes:
        """读原始字节 (可执行文件哈希用)."""

    @abstractmethod
    def list_dir(self, path: str) -> tuple[str, ...]:
        """列目录下的直接子项名 (不递归). 不是目录或不可读时返回空."""

    @abstractmethod
    def expand_glob(
        self,
        pattern: str,
        *,
        root: str,
        max_results: int | None = None,
        skip_ignored: bool = True,
    ) -> tuple[str, ...]:
        """按已冻结的视图展开 glob；max_results 用于限制枚举本身的资源消耗。

        `skip_ignored` 让实现跳过被忽略的路径, 判断由实现自己去问权威来源 (git 仓库里
        就是 git 自己). **必须在遍历时生效**, 不能展开完再过滤: max_results 数的是幸存
        的候选, 而不是走过的目录项. 反过来做的话, `.venv` 与 `.git` 会先把额度吃光,
        于是一次覆盖整个仓库的展开会报"结果不完整", 而被漏掉的恰好是源码.

        调用方只说"要不要跳过", 不说"跳过哪些": 跳过哪些是环境事实, 属于 infrastructure.
        """
