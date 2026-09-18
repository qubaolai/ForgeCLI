"""Read 给模型看的正文长什么样 (对应 readFileInRange.ts 与 FileReadTool 的渲染).

`FileState.content` 存的就是这个形状: 解码, CRLF -> LF, 从 offset 起最多 limit 行, 超长
行截断. 拿磁盘上的文件与缓存比 (Edit 的内容兜底, 变化通知的 diff, 压缩后重注入) 都要
经同一个函数, 否则比的是两种形状, 永远不相等. 放在 core: `conversation` 与 `tools` 都要
用, 而前者不许 import 后者.

纯函数, 不碰磁盘: 字节怎么来 (open, 注入的 read_bytes) 是调用方的事.
"""

from __future__ import annotations

__all__ = [
    "LINE_TRUNCATED_MARK",
    "MAX_LINES_TO_READ",
    "MAX_LINE_LENGTH",
    "add_line_numbers",
    "decode_text",
    "render_read",
]

# 没给 limit 时最多给多少行 (对应 MAX_LINES_TO_READ).
MAX_LINES_TO_READ = 2000
# 单行上限: 超过的截断 (压缩过的 JS 一行几十万字符, 全塞进上下文没有意义).
MAX_LINE_LENGTH = 2000
LINE_TRUNCATED_MARK = "… [line truncated]"


def decode_text(raw: bytes) -> str:
    """UTF-16 BOM 按 UTF-16, 否则 UTF-8, 再不行 latin-1 (永远解得开). CRLF -> LF."""
    if raw.startswith(b"\xff\xfe"):
        text = raw.decode("utf-16", errors="replace")
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
    return text.replace("\r\n", "\n")


def render_read(text: str, offset: int, limit: int | None) -> tuple[str, int]:
    """(给模型的正文, 文件总行数). offset 从 1 数; limit 为 None 取 MAX_LINES_TO_READ.

    正文非空时以换行结尾, 与 `cat` 一致.
    """
    all_lines = text.split("\n")
    if all_lines and all_lines[-1] == "":
        all_lines.pop()
    total = len(all_lines)
    start_index = max(offset - 1, 0)
    count = limit if limit is not None else MAX_LINES_TO_READ
    selected = all_lines[start_index : start_index + count]
    truncated: list[str] = []
    for line in selected:
        if len(line) > MAX_LINE_LENGTH:
            truncated.append(line[:MAX_LINE_LENGTH] + LINE_TRUNCATED_MARK)
        else:
            truncated.append(line)
    content = "\n".join(truncated)
    if truncated:
        content = content + "\n"
    return content, total


def add_line_numbers(content: str, start_line: int) -> str:
    """cat -n 的格式: 六位右对齐的行号加一个箭头."""
    if not content:
        return ""
    lines = content.split("\n")
    numbered: list[str] = []
    for index, line in enumerate(lines):
        if index == len(lines) - 1 and line == "":
            continue
        numbered.append(f"{start_line + index:6d}→{line}")
    return "\n".join(numbered)
