"""模型读过的那份是不是磁盘上这份 (对应 FileEditTool.ts / FileWriteTool.ts 的两次检查).

Edit / Write 各查两次. `validate_input` 一次, 在权限之前, 让模型早点拿到错;
`call` 里紧贴读盘再查一次: 权限询问可以等任意长, 用户在这期间用编辑器改了文件再点
allow, 第一次检查的结论已经过期. 第二次检查到落盘之间不再做别的 IO.

这里防的是外部写者 (编辑器, linter, Bash 子进程). 进程内的写者本来就是串行的: Edit /
Write 不进并发批 (`orchestration.partition_tool_calls`), 所以不加锁.

传进来的 `content` 必须来自 `read_text_file` (与 Read 同一个 `decode_text`), 否则内容
比对永远不相等. 传 None 表示不读盘, 只看 mtime (Write 的
validate_input: 它本来就不需要旧内容, 为一次兜底比对读整个文件不值; 误报的代价只是
模型再 Read 一次).
"""

from __future__ import annotations

import os
from enum import Enum

from forgecli.core.tool.file_state import FileStateCache
from forgecli.core.tool.file_text import decode_text

__all__ = [
    "Freshness",
    "MODIFIED_SINCE_READ_MESSAGE",
    "NOT_READ_MESSAGE",
    "UNEXPECTEDLY_MODIFIED_MESSAGE",
    "check_freshness",
    "read_text_file",
    "read_text_file_if_exists",
    "require_fresh",
]

NOT_READ_MESSAGE = "File has not been read yet. Read it first before writing to it."
MODIFIED_SINCE_READ_MESSAGE = (
    "File has been modified since read, either by the user or by a linter. "
    "Read it again before attempting to write it."
)
# call 里第二次检查失败用的措辞 (对应 FILE_UNEXPECTEDLY_MODIFIED_ERROR): 第一次已经
# 过了, 是权限询问期间被改的.
UNEXPECTEDLY_MODIFIED_MESSAGE = (
    "File has been unexpectedly modified. Read it again before attempting to write it."
)


class Freshness(Enum):
    NOT_READ = "not_read"  # 没读过, 或只看过注入的局部 (is_partial_view)
    MODIFIED = "modified"  # 读后 mtime 变了且内容不同


def read_text_file(path: str) -> str:
    with open(path, "rb") as stream:
        return decode_text(stream.read())


def read_text_file_if_exists(path: str) -> str | None:
    """不存在返回 None. 一次 open 代替 exists + open: 两步之间文件可以消失."""
    try:
        return read_text_file(path)
    except FileNotFoundError:
        return None


def check_freshness(
    path: str, content: str | None, cache: FileStateCache
) -> Freshness | None:
    """None = 模型读过的就是磁盘上这份."""
    state = cache.get(path)
    if state is None or state.is_partial_view:
        return Freshness.NOT_READ
    if os.path.getmtime(path) > state.timestamp:
        # Windows 上 mtime 会无故变 (云同步, 杀毒): 完整读过的按内容兜底.
        if content is not None and state.is_full_read and content == state.content:
            return None
        return Freshness.MODIFIED
    return None


def require_fresh(path: str, content: str, cache: FileStateCache) -> None:
    """call 里用: 不新鲜就抛, 由执行链归成一条 is_error 的 tool_result."""
    if check_freshness(path, content, cache) is not None:
        raise ValueError(UNEXPECTEDLY_MODIFIED_MESSAGE)
