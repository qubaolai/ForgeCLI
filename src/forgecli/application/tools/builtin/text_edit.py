"""补丁 FIND 段的片段定位: 逐字符对不上时, 按行对齐再试一次.

为什么不能只做逐字符匹配:

模型手里的 `old_string` 来自它上一次读到的内容, 而中间隔着一层它看不见的东西 —— 文件用
CRLF 还是 LF, 行尾有没有多余空格, 它复述这段代码时把整块的基准缩进带上了没有. 这几样都
不改变代码的含义, 却足以让逐字符比对失败. 失败之后模型能做的只有换个写法再试, 而它没有
任何证据能看出差在哪, 于是要么在同一处反复失败, 要么绕去 shell 改文件 —— 后者正是
ADR-0004 §14.6 要堵的那条路.

所以这里的判据是: **容忍不携带信息的差异, 不容忍携带信息的差异.**

- 行尾空白与换行符: 容忍. 它们在源码里没有语义.
- 整块**统一**的缩进偏移: 容忍, 并且把同样的偏移施加到 `new_string` 上. 模型少写或多写
  了一层公共缩进是一件事, 不是一处一处的错.
- 缩进方式不同 (文件用制表符而模型用空格), 或者块内相对缩进对不上: **不容忍**. 这时
  按偏移硬贴会产出缩进错乱的代码, 而 Python 与 YAML 里那就是改坏了. 这一档改为把文件里
  对应的原文逐字符引出来, 让模型照抄 —— 一次就能改对, 而不是继续猜.

对齐的结果最终会进 `ToolPlan.content_previews`, 所以人在审批界面上看到的始终是"文件会
变成什么样", 而不是一段还要再解释一次的替换意图. 这也是这里敢做容差的前提.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Alignment", "MatchOutcome", "locate"]

_WHITESPACE = " \t\v\f"


@dataclass(frozen=True)
class Alignment:
    """文件与 `old_string` 之间那一段统一的缩进差.

    两个字段最多只有一个非空: 要么文件比 `old_string` 多一段公共前缀 (模型少写了缩进),
    要么少一段 (模型多写了缩进).
    """

    add: str = ""
    drop: str = ""

    @property
    def shifted(self) -> bool:
        return bool(self.add or self.drop)

    def apply(self, line: str) -> str | None:
        """把同样的偏移施加到 `new_string` 的一行上; 施加不了返回 None."""
        if not line.strip():
            # 空行不带缩进信息, 也不该被补出一行只有空格的尾巴.
            return line
        if self.add:
            return self.add + line
        if self.drop:
            return line[len(self.drop) :] if line.startswith(self.drop) else None
        return line


@dataclass(frozen=True)
class MatchOutcome:
    """一次定位的结果.

    `spans` 为空时, `quoted` 可能带着"文件里其实长这样"的原文 —— 那是留给错误信息的,
    不是失败的附属说明, 而是下一次调用能一次成功的全部依据.
    """

    spans: tuple[tuple[int, int], ...] = ()
    alignment: Alignment = Alignment()
    line_ending_differs: bool = False
    quoted: str = ""
    conflict: str = ""
    # 写回时替换文本该用哪种换行. 只有走了按行对齐才非空 —— 逐字符命中说明模型给的片段
    # 与文件逐字节相同, 它自己写的换行就是对的, 不该被改。
    ending: str = ""


def locate(source: str, fragment: str) -> MatchOutcome:
    """在 source 里定位 fragment, 返回字符区间 [start, stop)."""
    if fragment in source:
        return MatchOutcome(spans=_exact_spans(source, fragment))

    lines = _split_lines(source)
    wanted = _split_lines(fragment)
    if not wanted:
        return MatchOutcome()

    aligned = _aligned_spans(lines, wanted)
    if aligned is not None:
        spans, alignment = aligned
        return MatchOutcome(
            spans=spans,
            alignment=alignment,
            line_ending_differs=_endings_of(lines) != _endings_of(wanted),
            ending=_dominant_ending(lines),
        )

    loose = _loose_windows(lines, wanted)
    if len(loose) == 1:
        return MatchOutcome(quoted=_quote(lines, loose[0], len(wanted)))
    if len(loose) > 1:
        return MatchOutcome(conflict=f"忽略空白差异后仍有 {len(loose)} 处相似片段")
    return MatchOutcome()


# ---- 按行切分, 保留每一行原本的行尾符 ----


@dataclass(frozen=True)
class _Line:
    body: str
    ending: str
    start: int

    @property
    def stop(self) -> int:
        return self.start + len(self.body) + len(self.ending)


def _split_lines(text: str) -> tuple[_Line, ...]:
    """切成 (正文, 行尾符, 起始偏移).

    保留行尾符而不是丢掉: 拼回去时未被替换的部分必须逐字节不变. 统一成一种换行再写回,
    等于顺手改掉了整个文件里我们压根没打算碰的那些行.
    """
    lines: list[_Line] = []
    start = 0
    length = len(text)
    while start < length:
        index = start
        while index < length and text[index] not in "\r\n":
            index += 1
        ending = ""
        if index < length:
            ending = "\r\n" if text.startswith("\r\n", index) else text[index]
        lines.append(_Line(text[start:index], ending, start))
        start = index + len(ending) if ending else length
    return tuple(lines)


def _endings_of(lines: tuple[_Line, ...]) -> frozenset[str]:
    return frozenset(line.ending for line in lines if line.ending)


def _dominant_ending(lines: tuple[_Line, ...]) -> str:
    """文件里用得最多的那种换行.

    替换文本必须跟着它: 模型永远写 `\n`, 直接贴进一个 CRLF 文件, 改动的那几行就变成了
    LF —— 文件从此混着两种换行, git diff 里整块都算改过, Windows 侧的工具也可能读岔.
    """
    counts: dict[str, int] = {}
    for line in lines:
        if line.ending:
            counts[line.ending] = counts.get(line.ending, 0) + 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda item: item[1])[0]


# ---- 逐字符命中 ----


def _exact_spans(source: str, fragment: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    start = source.find(fragment)
    while start >= 0:
        spans.append((start, start + len(fragment)))
        start = source.find(fragment, start + len(fragment))
    return tuple(spans)


# ---- 按行对齐 ----


def _aligned_spans(
    lines: tuple[_Line, ...], wanted: tuple[_Line, ...]
) -> tuple[tuple[tuple[int, int], ...], Alignment] | None:
    """找出所有"只差统一缩进与行尾空白"的窗口.

    要求所有窗口共用同一个偏移: 同一次调用在两处用了不同的缩进方式, 说明那两处本来就
    不是同一段代码, 一起替换掉的风险远大于便利.
    """
    width = len(wanted)
    spans: list[tuple[int, int]] = []
    agreed: Alignment | None = None
    for start in range(len(lines) - width + 1):
        alignment = _align(lines[start : start + width], wanted)
        if alignment is None:
            continue
        if agreed is None:
            agreed = alignment
        elif agreed != alignment:
            return None
        spans.append(_span_of(lines, start, wanted))
    if not spans or agreed is None:
        return None
    return tuple(spans), agreed


def _span_of(
    lines: tuple[_Line, ...], start: int, wanted: tuple[_Line, ...]
) -> tuple[int, int]:
    """窗口覆盖的字符区间.

    `old_string` 最后一行没有换行符时, 区间就到那一行正文结束为止 —— 把文件的换行也吞
    进去, 替换后这一行会和下一行粘在一起.
    """
    last = lines[start + len(wanted) - 1]
    if wanted[-1].ending:
        return lines[start].start, last.stop
    return lines[start].start, last.start + len(last.body)


def _align(window: tuple[_Line, ...], wanted: tuple[_Line, ...]) -> Alignment | None:
    add = ""
    drop = ""
    seen = False
    for actual, expected in zip(window, wanted, strict=True):
        left = actual.body.rstrip(_WHITESPACE)
        right = expected.body.rstrip(_WHITESPACE)
        if not right:
            # 空行只要求对面也是空行; 它不参与缩进推断, 否则一行空白就能否掉整段.
            if left:
                return None
            continue
        pair = _shift_between(left, right)
        if pair is None:
            return None
        if seen and pair != (add, drop):
            return None
        add, drop = pair
        seen = True
    return Alignment(add=add, drop=drop) if seen else None


def _shift_between(actual: str, expected: str) -> tuple[str, str] | None:
    """一行上的缩进差; 多出来的那一段必须全是空白, 否则就不是同一行."""
    if actual == expected:
        return "", ""
    if actual.endswith(expected):
        extra = actual[: len(actual) - len(expected)]
        return (extra, "") if _blank(extra) else None
    if expected.endswith(actual):
        extra = expected[: len(expected) - len(actual)]
        return ("", extra) if _blank(extra) else None
    return None


def _blank(text: str) -> bool:
    return bool(text) and not text.strip()


# ---- 只在错误信息里用: 把文件里对应的原文引出来 ----


def _loose_windows(
    lines: tuple[_Line, ...], wanted: tuple[_Line, ...]
) -> tuple[int, ...]:
    """忽略一切空白之后仍然逐行相同的窗口."""
    width = len(wanted)
    keys = [line.body.strip() for line in wanted]
    found: list[int] = []
    for start in range(len(lines) - width + 1):
        window = lines[start : start + width]
        if all(
            actual.body.strip() == key for actual, key in zip(window, keys, strict=True)
        ):
            found.append(start)
    return tuple(found)


_MAX_QUOTED_LINES = 24
_MAX_QUOTED_CHARS = 2000


def _quote(lines: tuple[_Line, ...], start: int, width: int) -> str:
    """带行号引出原文, 让模型照抄.

    引的是**文件里的字节**而不是一句"缩进对不上": 后者仍然要模型自己猜是几个空格还是
    制表符, 而它猜错过一次了.
    """
    shown = lines[start : start + min(width, _MAX_QUOTED_LINES)]
    body = "\n".join(
        f"{start + offset + 1:>6} | {line.body}" for offset, line in enumerate(shown)
    )
    if width > len(shown):
        body = f"{body}\n       | … 还有 {width - len(shown)} 行"
    return body[:_MAX_QUOTED_CHARS]
