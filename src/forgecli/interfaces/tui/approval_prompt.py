"""终端里的审批卡片与决议入口.

画的是与 Web 同一份 ``ApprovalView.to_payload()``: 用户在两条入口看到的目标, 脚本正文
与可选范围必须逐字一致 —— 各画各的话, 同一次调用在终端上少列一个目标, 而两边都不会
报错.

**不设等待超时**, 与 broker 的语义一致 (ADR-0025 决策 7): 终止条件只有用户做决定,
用户停止这一轮, 进程退出三个.
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

_SCOPE_LABELS = {
    "once": "允许这一次",
    "workspace": "本工作区始终允许",
}


def render_card(
    console: Console, view: Mapping[str, object], *, mandatory: bool
) -> None:
    """把一次待决议的调用完整摆出来."""
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
        rows.append(("工作区", " ".join(str(item) for item in roots)))
    mode = str(view.get("mode", ""))
    if mode:
        rows.append(("模式", mode))
    if rows:
        console.print(Padding(kv_table(rows), (0, 0, 0, 2)))

    _render_targets(console, view.get("target_groups"))
    _render_scripts(console, view.get("script_snapshots"))
    _render_previews(console, view.get("content_previews"))


def ask_decision(console: Console, view: Mapping[str, object]) -> str | None:
    """读一个决议. 返回 broker 认识的 ``once`` / ``workspace`` / ``deny``.

    选择方式是 ↑↓ + 回车, 与斜杠命令菜单一致; 数字键仍然直选, 照顾记得住"3 是拒绝"的
    人. 高亮初始落在"允许这一次"上 —— 它是范围最小的那一档.

    返回 None 表示用户按了 Esc 或 Ctrl-C, 那是"停止这一轮", **不是**"我拒绝". 判成拒绝
    会让模型收到一条用户从来没说过的拒绝, 并据此往下走; 而两者都不会放行, 所以按更
    保守的那个理解并不更安全, 只是更不诚实.
    """
    scopes = [
        str(item)
        for item in _as_list(view.get("allowed_scopes"))
        if str(item) in _SCOPE_LABELS
    ]
    options = [Option(scope, _SCOPE_LABELS[scope]) for scope in scopes]
    options.append(Option("deny", "拒绝"))
    console.print()
    blocked = str(view.get("learn_blocked_reason", ""))
    if blocked and "workspace" not in scopes:
        # 界面不自己推这句话: 只看得到 allowed_scopes 少了一档, 看不到少的是哪一条判据.
        console.print(Text(f"  不能选 [始终允许]: {blocked}", style=STYLE_DIM))
    console.print(Text("  Esc / Ctrl-C 停止这一轮", style=STYLE_DIM))
    try:
        picked = select_one(console, options)
    except SelectUnavailable:
        return _typed(console, options)
    return None if picked is None else picked.key


def _typed(console: Console, options: Sequence[Option]) -> str | None:
    """没有真终端时的回退: 打出选项再读一个编号.

    选项在交互路径上由 ``select_one`` 自己渲染, 所以这里必须补打一遍 —— 否则就是在问
    "请选择 [1/2/3]" 却没说 1/2/3 分别是什么.
    """
    for index, option in enumerate(options, start=1):
        console.print(Text(f"  {index}. {option.label}", style=STYLE_ACCENT))
    while True:
        try:
            raw = input("你的决定: ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print()
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1].key
        console.print(Text(f"输入 1-{len(options)} 之间的编号", style=STYLE_ERROR))


def _counts(raw: object) -> str:
    items = _as_list(raw)
    parts: list[str] = []
    for item in items:
        if isinstance(item, Mapping):
            parts.append(f"{item.get('label', '')} {item.get('count', 0)}")
    return " · ".join(parts)


def _render_targets(console: Console, raw: object) -> None:
    for group in _as_list(raw):
        if not isinstance(group, Mapping):
            continue
        paths = [str(item) for item in _as_list(group.get("paths"))]
        if not paths:
            continue
        label = str(group.get("label", ""))
        console.print(Padding(Text(label, style=STYLE_DIM), (0, 0, 0, 2)))
        shown = paths[:_MAX_TARGETS]
        for path in shown:
            console.print(Padding(Text(path), (0, 0, 0, 4)))
        if len(paths) > len(shown):
            console.print(
                Padding(
                    Text(f"另有 {len(paths) - len(shown)} 个", style=STYLE_DIM),
                    (0, 0, 0, 4),
                )
            )


def _render_scripts(console: Console, raw: object) -> None:
    for snapshot in _as_list(raw):
        if not isinstance(snapshot, Mapping):
            continue
        origin = str(snapshot.get("origin", ""))
        language = str(snapshot.get("language", ""))
        path = str(snapshot.get("path", "") or "")
        title = " · ".join(item for item in (language, origin, path) if item)
        console.print(Padding(Text(f"脚本 {title}", style=STYLE_DIM), (0, 0, 0, 2)))
        _render_source(console, str(snapshot.get("source", "")))


def _render_previews(console: Console, raw: object) -> None:
    for preview in _as_list(raw):
        if not isinstance(preview, Mapping):
            continue
        path = str(preview.get("path", ""))
        suffix = " (已截断)" if preview.get("truncated") else ""
        console.print(
            Padding(Text(f"写入 {path}{suffix}", style=STYLE_DIM), (0, 0, 0, 2))
        )
        _render_source(console, str(preview.get("content", "")))


def _render_source(console: Console, source: str) -> None:
    lines = source.splitlines()
    for line in lines[:_MAX_SOURCE_LINES]:
        console.print(Padding(Text(truncate(line, 160), style="white"), (0, 0, 0, 4)))
    if len(lines) > _MAX_SOURCE_LINES:
        console.print(
            Padding(
                Text(f"另有 {len(lines) - _MAX_SOURCE_LINES} 行", style=STYLE_DIM),
                (0, 0, 0, 4),
            )
        )


def _as_list(raw: object) -> Sequence[object]:
    return raw if isinstance(raw, list) else ()
