"""Edit 的内部函数: 定位 old_string, 施加替换, 取一段带行号的结果 (对应
FileEditTool/utils.ts).

定位分三步: 逐字相等; 引号归一化 (文件里是弯引号, 模型写的是直引号); 最后按行对齐
(行尾空白, 换行符, 整块统一的缩进偏移) —— 这一层是本仓库在 Claude Code 之上多出来的
容差 (ADR-0054 差异表), 命中时如实回报.
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.tools.builtin.Edit.matching import MatchOutcome, locate

__all__ = ["Located", "apply_edit", "find_actual_string", "snippet_around"]

_QUOTE_MAP = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
    }
)


@dataclass(frozen=True)
class Located:
    """定位结果. actual 是文件里真正要换掉的那段; note 是给模型的一句容差说明."""

    actual: str | None
    note: str = ""
    hint: str = ""
    aligned_new: str | None = None


def normalize_quotes(text: str) -> str:
    return text.translate(_QUOTE_MAP)


def find_actual_string(file_content: str, old_string: str, new_string: str) -> Located:
    if old_string in file_content:
        return Located(actual=old_string)
    normalized_file = normalize_quotes(file_content)
    normalized_old = normalize_quotes(old_string)
    index = normalized_file.find(normalized_old)
    if index != -1:
        return Located(
            actual=file_content[index : index + len(old_string)],
            note="matched after normalizing curly quotes",
        )
    outcome = locate(file_content, old_string)
    if outcome.spans:
        start, stop = outcome.spans[0]
        aligned_new = _align_new(new_string, outcome)
        notes: list[str] = []
        if outcome.alignment.shifted:
            notes.append("matched after aligning indentation")
        if outcome.line_ending_differs:
            notes.append("matched after normalizing line endings")
        return Located(
            actual=file_content[start:stop],
            note="; ".join(notes),
            aligned_new=aligned_new,
        )
    if outcome.quoted:
        return Located(
            actual=None,
            hint=(
                "The closest match in the file differs in whitespace or indentation. "
                "Copy it exactly:\n" + outcome.quoted
            ),
        )
    if outcome.conflict:
        return Located(actual=None, hint=outcome.conflict)
    return Located(actual=None)


def _align_new(new_string: str, outcome: MatchOutcome) -> str | None:
    if not outcome.alignment.shifted and not outcome.ending:
        return None
    lines = new_string.split("\n")
    shifted: list[str] = []
    for line in lines:
        adjusted = outcome.alignment.apply(line)
        if adjusted is None:
            return None
        shifted.append(adjusted)
    ending = outcome.ending or "\n"
    return ending.join(shifted)


def apply_edit(
    file_content: str, actual_old: str, new_string: str, *, replace_all: bool
) -> str:
    if actual_old == "":
        return new_string
    if replace_all:
        return file_content.replace(actual_old, new_string)
    return file_content.replace(actual_old, new_string, 1)


def snippet_around(content: str, actual_old: str, new_string: str) -> tuple[str, int]:
    """改动附近的一段 (前后各 4 行) 与它的起始行号, 给模型看结果."""
    index = content.find(new_string) if new_string else content.find(actual_old)
    if index == -1:
        index = 0
    before = content[:index]
    start_line = before.count("\n")
    changed_lines = new_string.count("\n") + 1
    lines = content.split("\n")
    first = max(start_line - 4, 0)
    last = min(start_line + changed_lines + 4, len(lines))
    return "\n".join(lines[first:last]), first + 1
