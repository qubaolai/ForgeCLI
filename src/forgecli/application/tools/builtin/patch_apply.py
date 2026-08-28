"""补丁段的替换机制与删除展开 (ADR-0029 C 类).

原先住在 `fs_write.py` 的 865 行里, 由五个写工具各自调用. 五个入口合并成
`fs_apply_patch` 之后它们搬到这里, 与 `locate` 放在一起 —— 定位与施加是同一件事的两半,
分在两个文件里的唯一后果是改一半忘另一半.

**容差必须原样保留**: 行尾空白, 换行符与整块统一的缩进偏移会自动对齐, 而做过的容差要
如实说给模型听. 模型手里那份 FIND 与文件并不一致, 不告诉它, 下一次它还会照着自己那份
去拼, 而下一次未必还落在容差范围内.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from forgecli.application.tools.builtin.patch_envelope import UpdateFile
from forgecli.application.tools.builtin.text_edit import (
    Alignment,
    MatchOutcome,
    locate,
)
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.application.workspace.filesystem_view import PathKind
from forgecli.domain.tool.errors import PreparationError, PreparationErrorCode

__all__ = ["Edit", "apply_replacements", "delete_targets"]

_EOL = re.compile(r"\r\n|\n|\r")
# 单文件替换的读取上限. 超过它的文件不适合整体读进内存做替换 —— 那种规模的改动应当
# 走 shell_run 里的流式工具 (ADR-0029 规则二).
_MAX_SOURCE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Edit:
    """要写进文件的完整内容, 外加一句"我做了什么容差"."""

    content: str
    note: str = ""


def apply_replacements(
    target: str,
    section: UpdateFile,
    context: ExecutionContext,
) -> Edit | PreparationError:
    """把 UPDATE 段里的多处替换依次算成最终文件内容.

    逐处顺序施加, 每处都在**上一处的结果**上定位: 同一个文件里两处替换可能互相靠近,
    在原始内容上各自算偏移再拼接会错位.

    错误里带段号与第几处 (ADR-0029 约束 3): 一封补丁里第三处替换定位失败时, 模型需要
    知道是第三处, 而不是"这封补丁不行".
    """
    facts = context.filesystem.facts(target)
    if facts.size > _MAX_SOURCE_BYTES:
        # 读到的是残缺前缀, 而替换的结果要整体写回去 —— 那会把文件截断.
        # 拒绝比"改一半"安全得多.
        return PreparationError(
            code=PreparationErrorCode.RESOURCE_LIMIT,
            message=(
                f"第 {section.index} 段: {target} 超过完整读取上限 "
                f"({facts.size} > {_MAX_SOURCE_BYTES} 字节), 拒绝基于残缺前缀改写. "
                "这种规模的改动请走 shell_run 里的流式工具."
            ),
            field_path="patch",
        )
    source = context.filesystem.read_text(target, max_bytes=_MAX_SOURCE_BYTES)
    content = source
    notes: list[str] = []
    for replacement in section.replacements:
        spot = f"第 {section.index} 段 第 {replacement.index} 处替换"
        outcome = locate(content, replacement.find)
        if not outcome.spans:
            return _not_found(target, content, replacement.find, outcome, spot)
        if len(outcome.spans) > 1 and not replacement.every:
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message=(
                    f"{spot}: FIND 片段在 {target} 中出现了 "
                    f"{len(outcome.spans)} 次. 请扩大片段使其唯一, "
                    "或者写 `*** FIND ALL` 明确要全部替换."
                ),
                field_path="patch",
            )
        written = _shift(
            _retype_endings(replacement.replace, outcome.ending), outcome.alignment
        )
        if written is None:
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message=(
                    f"{spot}: FIND 的缩进与 {target} 差一段公共前缀, 但 REPLACE 里有"
                    "几行去不掉这段前缀, 对齐之后会缩进错乱. "
                    "请照抄文件里的缩进重写这一处."
                ),
                field_path="patch",
            )
        content = _splice(content, outcome.spans, written)
        note = _note_for(outcome)
        if note:
            notes.append(f"第 {replacement.index} 处: {note}")
    return Edit(content=content, note="; ".join(notes))


def _retype_endings(new: str, ending: str) -> str:
    """把替换文本的换行换成文件在用的那种.

    模型永远写 `\n`. 直接贴进一个 CRLF 文件, 改动的那几行就成了 LF —— 文件混着两种换行,
    git diff 里整块都算改过. 只在走过按行对齐时才做: 逐字符命中说明模型给的片段与文件
    逐字节相同, 它写的换行本来就是对的.
    """
    if not ending or ending == "\n":
        return new
    return new.replace("\r\n", "\n").replace("\n", ending)


def _shift(new: str, alignment: Alignment) -> str | None:
    """把 old_string 上观察到的缩进偏移原样施加到 new_string.

    必须施加, 不能原样贴: 模型是用同一套缩进写出 old 和 new 的, old 少了一层公共缩进,
    new 就同样少一层. 只对齐匹配位置而把 new 原样写进去, 产出的是缩进错乱的代码.
    """
    if not alignment.shifted:
        return new
    shifted: list[str] = []
    for line in _EOL.split(new):
        moved = alignment.apply(line)
        if moved is None:
            return None
        shifted.append(moved)
    # 用 zip 把原来的换行原样接回去: split 丢掉的是哪一种, 拼回去就得是哪一种.
    endings = _EOL.findall(new)
    return "".join(
        piece + (endings[index] if index < len(endings) else "")
        for index, piece in enumerate(shifted)
    )


def _splice(source: str, spans: tuple[tuple[int, int], ...], written: str) -> str:
    """从后往前替换, 免得前面的替换把后面的偏移挪了."""
    content = source
    for start, stop in sorted(spans, reverse=True):
        content = content[:start] + written + content[stop:]
    return content


def _note_for(outcome: MatchOutcome) -> str:
    """做过哪些容差, 如实说给模型听."""
    reasons: list[str] = []
    if outcome.alignment.add:
        missing = len(outcome.alignment.add)
        reasons.append(f"old_string 比文件少了 {missing} 个字符的缩进")
    if outcome.alignment.drop:
        extra = len(outcome.alignment.drop)
        reasons.append(f"old_string 比文件多了 {extra} 个字符的缩进")
    if outcome.line_ending_differs:
        reasons.append("换行符不同")
    if not reasons:
        return ""
    return (
        f"注意: {', '.join(reasons)}, 已按文件里的原样对齐后替换. "
        "你手里那份内容与文件并不一致, 后续再改这个文件前请重新读一遍."
    )


def _not_found(
    target: str, source: str, old: str, outcome: MatchOutcome, spot: str
) -> PreparationError:
    if outcome.quoted:
        # 引原文而不是描述差异: 描述完模型仍然要猜是几个空格还是制表符, 而它刚猜错过.
        return PreparationError(
            code=PreparationErrorCode.INVALID_INPUT,
            message=(
                f"{spot}: 在 {target} 中找不到 FIND 片段 —— 忽略空白之后能对上一处, "
                "但缩进方式不一致 (制表符与空格, 或块内相对缩进不同), "
                "按偏移硬贴会写出缩进错乱的代码. 文件里那一段逐字符是:\n"
                f"{outcome.quoted}\n"
                "请照抄上面这几行 (去掉行号与竖线) 作为 FIND 片段再试."
            ),
            field_path="patch",
        )
    detail = f" {outcome.conflict}." if outcome.conflict else _miss_hint(source, old)
    return PreparationError(
        code=PreparationErrorCode.INVALID_INPUT,
        message=(
            f"{spot}: 在 {target} 中找不到 FIND 片段. 它必须与文件内容逐字符一致, "
            f"包括缩进与换行.{detail}"
        ),
        field_path="patch",
    )


def _miss_hint(source: str, old: str) -> str:
    """连按行对齐都没命中时, 指出**最可能的原因**.

    只说找不到, 模型唯一能做的就是换个写法再试一次 —— 而它没有任何证据能看出差在哪.
    判断顺序按"改起来最省事"排: 先看空白, 再定位行号, 最后才让它把文件重读一遍.
    """
    if old != old.strip() and old.strip() and old.strip() in source:
        return " 去掉首尾空白后能命中: 多半是首尾的换行或缩进多带了一点."
    nearest = _nearest_block(source, old)
    if nearest:
        return nearest
    lines = old.splitlines()
    first = lines[0].strip() if lines else ""
    hits = [
        index + 1
        for index, line in enumerate(source.splitlines())
        if _shares_head(line.strip(), first)
    ]
    if hits:
        listed = ", ".join(str(item) for item in hits[:5])
        more = " 等" if len(hits) > 5 else ""
        return (
            f" 第 {listed}{more} 行与 old_string 的首行开头相同, "
            "可以先读这一段再重试."
        )
    return " 先用 fs_read 读回当前内容, 再照抄其中一段作为 old_string."


# 相似度低于这个值就不引了: 引一段其实不相干的代码, 比说"找不到"更糟 —— 模型会照着它
# 改, 然后第二次定位失败在另一个位置.
_NEAREST_FLOOR = 55.0
# 滑窗扫描的行数上限. 超过就不找了 —— 这段跑在 prepare 里, 而 prepare 在裁决之前, 卡在
# 那里没有任何进度可以显示.
_NEAREST_MAX_LINES = 20_000


def _nearest_block(source: str, old: str) -> str:
    """把文件里**最像** FIND 片段的那一段引出来, 附上相似度.

    这一支补的是 `_miss_hint` 原来的兜底: "先用 fs_read 读回当前内容". 那句话是对的但
    没有信息 —— 模型手里那份 FIND 与文件哪里不一样, 它依然看不见, 于是最省力的下一步
    是把整个文件读回来再猜一次. 在真实会话里 110 次 fs_apply_patch 有 17 次
    apply_failed, 而且集中成一串: 定位失败 -> 重读 -> 再失败.

    相似度用 `rapidfuzz`: 逐窗算编辑距离比是 O(n*m) 的字符串比较, 纯 Python 写在这里会
    让一个几千行的文件卡住 prepare. rapidfuzz 是 C++ 实现, 并且 `partial_ratio` 自带
    "在长串里找最像的一段"这个语义, 正好是这里要问的问题.
    """
    source_lines = source.splitlines()
    old_lines = old.splitlines()
    if not old_lines or not source_lines or len(source_lines) > _NEAREST_MAX_LINES:
        return ""
    span = len(old_lines)
    best_score = 0.0
    best_start = 0
    for start in range(max(1, len(source_lines) - span + 1)):
        window = "\n".join(source_lines[start : start + span])
        score = fuzz.ratio(window, old, score_cutoff=best_score)
        if score > best_score:
            best_score, best_start = score, start
    if best_score < _NEAREST_FLOOR:
        return ""
    quoted = "\n".join(
        f"{best_start + offset + 1:>6} | {line}"
        for offset, line in enumerate(source_lines[best_start : best_start + span])
    )
    return (
        f" 文件里最接近的一段在第 {best_start + 1} 行 (相似度 {best_score:.0f}%), "
        f"逐字符是:\n{quoted}\n"
        "请照抄上面这几行 (去掉行号与竖线) 作为 FIND 片段再试."
    )


_HEAD_PROBE = 12
_HEAD_FLOOR = 4


def _shares_head(line: str, first: str) -> bool:
    """两行是不是同一行的两个版本.

    双向判断: 模型既可能比文件多带了尾部 (顺手补了注释), 也可能少带一截. 只按一个方向
    比, 另一半情况就报不出行号, 而那正是最常见的一种 —— 模型凭记忆复述了一行代码.
    """
    if len(line) < _HEAD_FLOOR or len(first) < _HEAD_FLOOR:
        return False
    return line.startswith(first[:_HEAD_PROBE]) or first.startswith(line[:_HEAD_PROBE])


def delete_targets(
    context: ExecutionContext, root: str
) -> tuple[str, ...] | PreparationError:
    """递归列出目录、空目录和文件，符号链接一律拒绝。

    走 FileSystemView 而不是直接 os.walk: 展开必须基于这次调用冻结的那一份视图, 否则
    "审批时看到的清单"与"执行时真删的东西"可能不是一回事.
    """
    files: list[str] = []
    directories: list[str] = [root]
    stack = [root]
    seen: set[str] = set()
    while stack:
        current = stack.pop()
        if current in seen:
            # 目录里有指回上层的符号链接时不至于转圈.
            continue
        seen.add(current)
        for name in context.filesystem.list_dir(current):
            child = f"{current}/{name}"
            facts = context.filesystem.facts(child)
            if facts.is_symlink:
                return PreparationError(
                    code=PreparationErrorCode.UNSUPPORTED_REQUEST,
                    message=f"目录树包含符号链接，拒绝递归删除: {child}",
                    field_path="patch",
                )
            if facts.kind is PathKind.DIRECTORY:
                directory = facts.realpath or child
                directories.append(directory)
                stack.append(directory)
            elif facts.exists:
                files.append(facts.realpath or child)
    return (*tuple(sorted(directories)), *tuple(sorted(files)))
