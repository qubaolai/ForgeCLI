"""补丁信封的语法与解析 (ADR-0029 规则三 C 类).

一个信封 = 若干文件段, 每段一种操作. 五个写工具合并成一个入口之后, 未来所有写入场景的
变化都在这里的语法里表达, 不占新工具位, 不加新参数.

    *** UPDATE src/a.py
    *** FIND
    def old():
        return 1
    *** REPLACE
    def new():
        return 2
    *** FIND ALL
    x = 1
    *** REPLACE
    x = 2

    *** NEW src/b.py
    print("hello")

    *** DELETE src/c.py

    *** MOVE src/d.py -> src/e.py

**不抄 Codex 的 `+`/`-` 行前缀格式** (ADR-0029 决策). 那套格式的优势来自模型对它的
训练, 而那主要是 OpenAI 系模型的优势; Forge 走多供应商网关, 对 DeepSeek / Qwen 未必
成立. 更要紧的是**逐字正文换来了可诊断性**: FIND 段就是文件里的原文, 因此定位失败时
可以直接复用 `text_edit.locate` 的容差与"文件里其实长这样"的原文回显 —— 那是 ADR-0029
点名要保住的东西. 带前缀的格式做不到, 因为前缀本身要先被剥掉才能比对.

语法规则只有三条:

1. **标记是行锚定的**: 一行以 `*** ` 开头且其后是已知动词时才是标记, 否则是正文.
   于是正文里出现 `*** 注意` 这种行不会被误认.
2. **正文逐字**: 标记行的换行之后, 到下一个标记行之前的那个换行为止. 想要末尾换行就
   多空一行.
3. **解析失败指到段和处**: 错误里带段序号, 路径与第几处替换, 不整封退回 (ADR-0029
   约束 3).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "DeleteFile",
    "MoveFile",
    "NewFile",
    "PatchSection",
    "PatchSyntaxError",
    "Replacement",
    "UpdateFile",
    "parse_envelope",
]

_MARKER = "*** "
_MOVE_ARROW = " -> "


@dataclass(frozen=True)
class Replacement:
    """UPDATE 段里的一处替换. `index` 从 1 起, 用来定位错误.

    `every` 对应 `*** FIND ALL`: 片段命中多处时全部替换. 默认 (`*** FIND`) 要求唯一,
    命中多处就报错并给出次数 —— 因为"我以为只有一处"和"我要改所有处"是两个意图, 猜错
    任何一个都会写坏文件.

    这是**语法里的一个变体, 不是工具的一个参数** (ADR-0029 规则一): 加参数会让每次
    调用都要读它, 加语法只在用到时才出现.
    """

    find: str
    replace: str
    index: int
    every: bool = False


@dataclass(frozen=True)
class NewFile:
    path: str
    content: str
    index: int


@dataclass(frozen=True)
class UpdateFile:
    path: str
    replacements: tuple[Replacement, ...]
    index: int


@dataclass(frozen=True)
class DeleteFile:
    path: str
    index: int


@dataclass(frozen=True)
class MoveFile:
    source: str
    target: str
    index: int


PatchSection = NewFile | UpdateFile | DeleteFile | MoveFile


class PatchSyntaxError(Exception):
    """信封语法错误. `where` 指到具体的段与处, 供 PreparationError 直接引用."""

    def __init__(self, message: str, *, where: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.where = where

    def described(self) -> str:
        return f"{self.where}: {self.message}" if self.where else self.message


@dataclass(frozen=True)
class _Marker:
    verb: str
    argument: str
    line_number: int


def _marker_at(line: str, line_number: int) -> _Marker | None:
    """这一行是不是标记. 不是已知动词就当正文 —— 正文里的 `*** 注意` 不该被误认."""
    if not line.startswith(_MARKER):
        return None
    rest = line[len(_MARKER) :].strip()
    verb, _, argument = rest.partition(" ")
    upper = verb.upper()
    if upper not in {"UPDATE", "NEW", "DELETE", "MOVE", "FIND", "REPLACE"}:
        return None
    return _Marker(verb=upper, argument=argument.strip(), line_number=line_number)


def parse_envelope(text: str) -> tuple[PatchSection, ...]:
    """把信封解析成文件段. 语法错误抛 PatchSyntaxError."""
    if not text.strip():
        raise PatchSyntaxError("补丁信封是空的")
    lines = text.split("\n")
    markers = [
        marker
        for index, line in enumerate(lines)
        if (marker := _marker_at(line, index)) is not None
    ]
    if not markers:
        raise PatchSyntaxError(
            "没有找到任何 `*** UPDATE/NEW/DELETE/MOVE` 段. "
            "标记必须独占一行且从行首开始."
        )
    if markers[0].verb in {"FIND", "REPLACE"}:
        raise PatchSyntaxError(
            f"`*** {markers[0].verb}` 出现在任何文件段之前",
            where=f"第 {markers[0].line_number + 1} 行",
        )

    sections: list[PatchSection] = []
    position = 0
    while position < len(markers):
        section, position = _read_section(lines, markers, position, len(sections) + 1)
        sections.append(section)
    return tuple(sections)


def _read_section(
    lines: list[str], markers: list[_Marker], position: int, ordinal: int
) -> tuple[PatchSection, int]:
    marker = markers[position]
    where = f"第 {ordinal} 段 (`*** {marker.verb} {marker.argument}`)"
    if not marker.argument:
        raise PatchSyntaxError(f"`*** {marker.verb}` 后面没有路径", where=where)

    if marker.verb == "DELETE":
        return DeleteFile(path=marker.argument, index=ordinal), position + 1

    if marker.verb == "MOVE":
        source, arrow, target = marker.argument.partition(_MOVE_ARROW)
        if not arrow or not source.strip() or not target.strip():
            raise PatchSyntaxError(
                "MOVE 段要写成 `*** MOVE 源路径 -> 目标路径`", where=where
            )
        return (
            MoveFile(source=source.strip(), target=target.strip(), index=ordinal),
            position + 1,
        )

    if marker.verb == "NEW":
        body = _body(lines, marker, _next_marker(markers, position))
        return NewFile(path=marker.argument, content=body, index=ordinal), position + 1

    if marker.verb == "UPDATE":
        return _read_update(lines, markers, position, ordinal, where)

    raise PatchSyntaxError(f"`*** {marker.verb}` 不能开启一个文件段", where=where)


def _read_update(
    lines: list[str],
    markers: list[_Marker],
    position: int,
    ordinal: int,
    where: str,
) -> tuple[PatchSection, int]:
    path = markers[position].argument
    cursor = position + 1
    replacements: list[Replacement] = []
    while cursor < len(markers) and markers[cursor].verb == "FIND":
        nth = len(replacements) + 1
        spot = f"{where} 第 {nth} 处替换"
        if cursor + 1 >= len(markers) or markers[cursor + 1].verb != "REPLACE":
            raise PatchSyntaxError("`*** FIND` 之后必须紧跟 `*** REPLACE`", where=spot)
        argument = markers[cursor].argument.upper()
        if argument and argument != "ALL":
            raise PatchSyntaxError(
                f"`*** FIND` 只接受可选的 ALL, 不认识 `{markers[cursor].argument}`",
                where=spot,
            )
        find = _body(lines, markers[cursor], markers[cursor + 1])
        replace = _body(lines, markers[cursor + 1], _next_marker(markers, cursor + 1))
        if not find:
            raise PatchSyntaxError("FIND 段是空的, 无法定位", where=spot)
        replacements.append(
            Replacement(find=find, replace=replace, index=nth, every=argument == "ALL")
        )
        cursor += 2
    if not replacements:
        raise PatchSyntaxError(
            "UPDATE 段里没有任何 `*** FIND` / `*** REPLACE`. "
            "整体重写文件请改用 `*** NEW`.",
            where=where,
        )
    if cursor < len(markers) and markers[cursor].verb == "REPLACE":
        raise PatchSyntaxError(
            "`*** REPLACE` 前面没有对应的 `*** FIND`",
            where=f"{where} 第 {len(replacements) + 1} 处替换",
        )
    section = UpdateFile(path=path, replacements=tuple(replacements), index=ordinal)
    return section, cursor


def _next_marker(markers: list[_Marker], position: int) -> _Marker | None:
    return markers[position + 1] if position + 1 < len(markers) else None


def _body(lines: list[str], start: _Marker, stop: _Marker | None) -> str:
    """标记行之后到下一个标记行之前的原文.

    末尾那个换行属于分隔, 不属于正文 —— 想要末尾换行就多空一行. 这条规则要写死:
    含糊的话每个新建文件都会莫名其妙多或少一个换行, 而 diff 里看不出来.
    """
    first = start.line_number + 1
    last = stop.line_number if stop is not None else len(lines)
    return "\n".join(lines[first:last])
