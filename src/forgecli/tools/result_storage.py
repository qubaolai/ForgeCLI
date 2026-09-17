"""超长结果落盘 (ADR-0054 决策 5; 对应 utils/toolResultStorage.ts).

文本超过 min(tool.max_result_size_chars, 50000) 时写到 tool-results/<tool_use_id>.txt,
回给模型前 2000 字节的预览与路径, 模型用 Read 读全文. Read 自己声明无穷大, 永不落盘.
"""

from __future__ import annotations

import math
from pathlib import Path

from forgecli.common.json_io import write_atomic
from forgecli.common.observability.log import get_log
from forgecli.core.tool.tool import DEFAULT_MAX_RESULT_SIZE_CHARS, Tool

__all__ = [
    "PERSISTED_OUTPUT_TAG",
    "PREVIEW_SIZE_BYTES",
    "persistence_threshold",
    "persist_if_needed",
]

_log = get_log(__name__)

PERSISTED_OUTPUT_TAG = "<persisted-output>"
_PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"
PREVIEW_SIZE_BYTES = 2000


def persistence_threshold(tool: Tool) -> float:
    declared = tool.max_result_size_chars
    if math.isinf(declared):
        return declared
    return min(declared, DEFAULT_MAX_RESULT_SIZE_CHARS)


def persist_if_needed(
    text: str, tool: Tool, *, tool_use_id: str, tool_results_dir: str
) -> tuple[str, bool]:
    """返回 (回给模型的文本, 是否落盘了)."""
    threshold = persistence_threshold(tool)
    if math.isinf(threshold) or len(text) <= threshold:
        return text, False
    path = Path(tool_results_dir) / f"{tool_use_id}.txt"
    try:
        write_atomic(path, text)
    except OSError:
        _log.exception("tools.persist_failed", tool=tool.name, path=str(path))
        return text[: int(threshold)], False
    preview = text.encode("utf-8")[:PREVIEW_SIZE_BYTES].decode("utf-8", errors="ignore")
    total = len(text.encode("utf-8"))
    wrapped = (
        f"{PERSISTED_OUTPUT_TAG}\n"
        f"Full output saved to: {path}\n"
        f"Total size: {total} bytes. Preview (first {PREVIEW_SIZE_BYTES} bytes):\n\n"
        f"{preview}\n"
        f"{_PERSISTED_OUTPUT_CLOSING_TAG}"
    )
    return wrapped, True
