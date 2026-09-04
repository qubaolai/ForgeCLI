"""终端里的提示卡片与作答入口 (ADR-0043 决策 11).

一条待答提示可能是一次安全审批, 也可能是模型的一次提问 —— 它们走同一条队列, 在这里按
``kind`` 分两个渲染分支.

审批分支画的是与 Web 同一份 ``ApprovalView.to_payload()`` (躺在 ``detail`` 里): 用户在
两条入口看到的目标, 脚本正文与可选范围必须逐字一致 —— 各画各的话, 同一次调用在终端上
少列一个目标, 而两边都不会报错.

选项文案也不在这里写死: 它随提示一起来自 ``ApprovalService``, 否则同一个选项在
终端与 Web 上会叫两个名字.

**不设等待超时**, 与通道的语义一致: 终止条件只有用户做决定, 用户停止这一轮, 进程退出
三个.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from rich.console import Console
from rich.padding import Padding
from rich.text import Text

from forgecli.interfaces.tui.console import (
    ASK,
    STYLE_ACCENT,
    STYLE_DIM,
    STYLE_ERROR,
    STYLE_WARN,
    display_path,
    kv_table,
    truncate,
)
from forgecli.interfaces.tui.select import Option, SelectUnavailable, select_one

# 目标清单在卡片里最多列这么多条; 超出的只报剩余条数. 一次 glob 命中几百个文件时,
# 全列出来会把决议入口顶出屏幕 —— 而那正是用户此刻要按的东西.
_MAX_TARGETS = 12
# 脚本正文与写入内容的行数上限. 超出部分不省略成"...", 而是明说还有多少行:
# 一个看不出被截断过的正文, 用户会以为自己批准的就是这些.
_MAX_SOURCE_LINES = 40
_SUMMARY_TARGETS = 4
_SUMMARY_SOURCE_LINES = 8

# 自己写一句. 与任何一个后端选项都不会撞: 后端的 value 来自 ApprovalScope 或模型给的
# 选项值, 两者都不会是这个形状.
_FREE_TEXT = "__text__"
_DETAILS = "__details__"


def render_card(console: Console, prompt: Mapping[str, object]) -> None:
    """把一条待答提示完整摆出来."""
    if str(prompt.get("kind", "")) == "question":
        _render_question(console, prompt)
        return
    detail = prompt.get("detail")
    _render_approval(console, detail if isinstance(detail, Mapping) else {})


def _render_question(console: Console, prompt: Mapping[str, object]) -> None:
    """模型的一次提问.

    正文按**纯文本**打, 不走 Markdown (ADR-0043 决策 6): 这段文案是模型写的, 而 Markdown
    能藏链接 —— 一张由模型控制文案的卡片正是钓鱼最省事的位置.
    """
    console.print()
    console.print(Text(f"{ASK} 需要你回答", style=f"bold {STYLE_WARN}"))
    console.print(Padding(Text(str(prompt.get("title", ""))), (0, 0, 0, 2)))
    body = str(prompt.get("body", ""))
    if body:
        console.print(Padding(Text(body, style=STYLE_DIM), (0, 0, 0, 2)))


def _render_approval(console: Console, view: Mapping[str, object]) -> None:
    """一次待决议的调用."""
    mandatory = bool(view.get("mandatory"))
    tool_name = str(view.get("tool_name", ""))
    console.print()
    header = Text(f"{ASK} 需要你确认  ", style=f"bold {STYLE_WARN}")
    header.append(tool_name, style=f"bold {STYLE_ACCENT}")
    if mandatory:
        # Mandatory Ask 只能一次性批准 (ADR-0013 §4.1). 不标出来的话, 用户会把"这次
        # 没有始终允许"当成界面漏了一个选项.
        header.append("  必须逐次确认", style=STYLE_WARN)
    console.print(header)

    rows: list[tuple[str, str]] = []
    raw_command = str(view.get("raw_command", ""))
    if raw_command:
        rows.append(("命令", raw_command))
    counts = _counts(view.get("counts"))
    if counts:
        rows.append(("目标", counts))
    resolution = str(view.get("target_resolution", ""))
    if resolution:
        rows.append(("封闭度", resolution))
    unresolved = view.get("unresolved_reason")
    if unresolved:
        rows.append(("未封闭", str(unresolved)))
    roots = view.get("workspace_roots")
    if isinstance(roots, list) and roots:
        rows.append(("工作区", " ".join(display_path(str(item)) for item in roots)))
    mode = str(view.get("mode", ""))
    if mode:
        rows.append(("模式", mode))
    if rows:
        console.print(Padding(kv_table(rows), (0, 0, 0, 2)))

    _render_details(
        console,
        view,
        target_limit=_SUMMARY_TARGETS,
        source_limit=_SUMMARY_SOURCE_LINES,
    )
    console.print(Text("  可在决议菜单中查看更多详情；默认焦点为拒绝", style=STYLE_DIM))


def ask_decision(
    console: Console, prompt: Mapping[str, object]
) -> tuple[str, str] | None:
    """读一次作答. 返回 ``(choice, text)``, 两者都直接交给通道.

    选择方式是 ↑↓ + 回车, 与斜杠命令菜单一致; 数字键仍然直选, 照顾记得住"3 是拒绝"的
    人.

    **审批的高亮初始落在最后一项 (拒绝)**: 回车不会在用户还没移动光标时产生授权.
    提问没有这个顾虑 —— 它的任何一项都不产生授权 —— 所以焦点落在第一项.

    返回 None 表示用户按了 Esc 或 Ctrl-C, 那是"停止这一轮", **不是**"我拒绝". 判成拒绝
    会让模型收到一条用户从来没说过的拒绝, 并据此往下走; 而两者都不会放行, 所以按更
    保守的那个理解并不更安全, 只是更不诚实.
    """
    question = str(prompt.get("kind", "")) == "question"
    detail = prompt.get("detail")
    view: Mapping[str, object] = detail if isinstance(detail, Mapping) else {}
    options = [
        Option(str(item.get("value", "")), str(item.get("label", "")))
        for item in _as_list(prompt.get("choices"))
        if isinstance(item, Mapping)
    ]
    free_text = bool(prompt.get("free_text"))
    console.print()
    if not question:
        blocked = str(view.get("learn_blocked_reason", ""))
        if blocked and not any(item.key == "workspace" for item in options):
            # 界面不自己推这句话: 只看得到少了一档, 看不到少的是哪一条判据.
            console.print(Text(f"  不能选 [始终允许]: {blocked}", style=STYLE_DIM))
    console.print(Text("  Esc / Ctrl-C 停止这一轮", style=STYLE_DIM))
    if question and not options:
        # 只收自由文本的提问: 摆一个只有一项的菜单是在让人多按一次回车.
        return _typed_answer(console)
    try:
        while True:
            interactive: list[Option] = []
            if not question:
                interactive.append(Option(_DETAILS, "查看更多详情", "不执行"))
            interactive.extend(options)
            if free_text:
                interactive.append(Option(_FREE_TEXT, "自己写一句"))
            picked = select_one(
                console,
                interactive,
                default_index=0 if question else len(interactive) - 1,
            )
            if picked is None:
                return None
            if picked.key == _DETAILS:
                console.print()
                console.print(Text("完整审批详情", style=f"bold {STYLE_ACCENT}"))
                _render_details(
                    console,
                    view,
                    target_limit=_MAX_TARGETS,
                    source_limit=_MAX_SOURCE_LINES,
                )
                continue
            if picked.key == _FREE_TEXT:
                return _typed_answer(console)
            return (picked.key, "")
    except SelectUnavailable:
        return _typed(console, options, free_text=free_text)


def _typed(
    console: Console, options: Sequence[Option], *, free_text: bool
) -> tuple[str, str] | None:
    """没有真终端时的回退: 打出选项再读一个编号.

    选项在交互路径上由 ``select_one`` 自己渲染, 所以这里必须补打一遍 —— 否则就是在问
    "请选择 [1/2/3]" 却没说 1/2/3 分别是什么.
    """
    if not options:
        return _typed_answer(console)
    for index, option in enumerate(options, start=1):
        console.print(Text(f"  {index}. {option.label}", style=STYLE_ACCENT))
    if free_text:
        console.print(Text("  或者直接写一句", style=STYLE_DIM))
    while True:
        try:
            raw = input("你的决定: ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print()
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return (options[int(raw) - 1].key, "")
        if free_text and raw:
            # 自由文本恒可答 (ADR-0043 决策 7): 一个都不合适的选项集合不该把人锁住.
            return ("", raw)
        console.print(Text(f"输入 1-{len(options)} 之间的编号", style=STYLE_ERROR))


def _typed_answer(console: Console) -> tuple[str, str] | None:
    """读一行自由文本. 空行等同没答, 继续问."""
    while True:
        try:
            raw = input("你的回答: ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print()
            return None
        if raw:
            return ("", raw)
        console.print(Text("写一句再回车, 或按 Ctrl-C 停止这一轮", style=STYLE_ERROR))


def _counts(raw: object) -> str:
    items = _as_list(raw)
    parts: list[str] = []
    for item in items:
        if isinstance(item, Mapping):
            parts.append(f"{item.get('label', '')} {item.get('count', 0)}")
    return " · ".join(parts)


def _render_details(
    console: Console,
    view: Mapping[str, object],
    *,
    target_limit: int,
    source_limit: int,
) -> None:
    _render_targets(console, view.get("target_groups"), limit=target_limit)
    _render_scripts(console, view.get("script_snapshots"), limit=source_limit)
    _render_previews(console, view.get("content_previews"), limit=source_limit)


def _render_targets(
    console: Console, raw: object, *, limit: int = _MAX_TARGETS
) -> None:
    for group in _as_list(raw):
        if not isinstance(group, Mapping):
            continue
        paths = [str(item) for item in _as_list(group.get("paths"))]
        if not paths:
            continue
        label = str(group.get("label", ""))
        console.print(Padding(Text(label, style=STYLE_DIM), (0, 0, 0, 2)))
        shown = paths[:limit]
        for path in shown:
            console.print(Padding(Text(path), (0, 0, 0, 4)))
        if len(paths) > len(shown):
            console.print(
                Padding(
                    Text(f"另有 {len(paths) - len(shown)} 个", style=STYLE_DIM),
                    (0, 0, 0, 4),
                )
            )


def _render_scripts(
    console: Console, raw: object, *, limit: int = _MAX_SOURCE_LINES
) -> None:
    for snapshot in _as_list(raw):
        if not isinstance(snapshot, Mapping):
            continue
        origin = str(snapshot.get("origin", ""))
        language = str(snapshot.get("language", ""))
        path = str(snapshot.get("path", "") or "")
        title = " · ".join(item for item in (language, origin, path) if item)
        console.print(Padding(Text(f"脚本 {title}", style=STYLE_DIM), (0, 0, 0, 2)))
        _render_source(console, str(snapshot.get("source", "")), limit=limit)


def _render_previews(
    console: Console, raw: object, *, limit: int = _MAX_SOURCE_LINES
) -> None:
    for preview in _as_list(raw):
        if not isinstance(preview, Mapping):
            continue
        path = str(preview.get("path", ""))
        suffix = " (已截断)" if preview.get("truncated") else ""
        console.print(
            Padding(Text(f"写入 {path}{suffix}", style=STYLE_DIM), (0, 0, 0, 2))
        )
        _render_source(console, str(preview.get("content", "")), limit=limit)


def _render_source(
    console: Console, source: str, *, limit: int = _MAX_SOURCE_LINES
) -> None:
    lines = source.splitlines()
    for line in lines[:limit]:
        console.print(Padding(Text(truncate(line, 160), style="white"), (0, 0, 0, 4)))
    if len(lines) > limit:
        console.print(
            Padding(
                Text(f"另有 {len(lines) - limit} 行", style=STYLE_DIM),
                (0, 0, 0, 4),
            )
        )


def _as_list(raw: object) -> Sequence[object]:
    return raw if isinstance(raw, list) else ()
