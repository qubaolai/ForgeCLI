"""/config: 配置面.

条目, 中文名, 说明, 取值范围全部来自 `domain/config/config_keys` 的 SCHEMA. 这里一个
字面量都不写: 同一个开关在终端和网页上叫不同的名字, 两边都不会报错.

**按前缀分类, ←/→ 切换.** 九项排成一条要从头扫到尾, 而它们本来就按前缀分成界面, 日志,
执行几类. 分类表由前缀派生而不是手写一张清单 —— 新加的配置项自动落进它的那一类, 手写
清单则会让它悄悄掉进"其他".

模型, 供应商与网关不在 SCHEMA 里 (它们住 llm.json, 各有各的校验), 但用户是照着 Web 的
设置页来找的, 而那一页把常规和模型放在同一处. 所以"模型"这一类里挂三个入口过去 ——
只列 SCHEMA 的话, 一个来这里找"添加供应商"的人会得出"这个 CLI 加不了供应商"的结论.
"""

from __future__ import annotations

from rich.text import Text

from forgecli.domain.config import config_keys
from forgecli.domain.config.config_keys import ConfigKey, ValueKind
from forgecli.interfaces.tui.chooser import (
    Group,
    Option,
    ask_text,
    choose,
    choose_grouped,
    confirm,
)
from forgecli.interfaces.tui.commands import model as model_commands
from forgecli.interfaces.tui.commands import provider as provider_commands
from forgecli.interfaces.tui.commands.context import CommandContext
from forgecli.interfaces.tui.console import STYLE_DIM, error, ok
from forgecli.shared.errors import ConfigValidationError

# 键名前缀 -> 分类名. 顺序即分类的先后.
_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("output", "界面"),
    ("logging", "日志"),
    ("execution", "执行"),
    ("model", "模型"),
)
_OTHER = "其他"

# 挂在"模型"分类下的三个跳转. 它们不是配置项, 所以 key 用双下划线圈起来, 与 SCHEMA 的
# 键名不可能撞上.
_JUMPS: tuple[tuple[str, str, str], ...] = (
    ("__model__", "模型", "选择, 添加与编辑; 同 /model"),
    ("__provider__", "供应商", "添加与编辑接入点; 同 /provider"),
    ("__gateway__", "网关运行时", "缓存, 熔断与重试; 同 /gateway"),
)
_JUMP_CATEGORY = "模型"


def cmd_config(context: CommandContext, argument: str) -> None:
    """菜单循环到 Esc 为止: 改完一项回到这一层, 而不是回到提示符."""
    if not context.require_idle():
        return
    raw = argument.strip().split(maxsplit=1)
    if raw:
        item = next(
            (entry for entry in config_keys.SCHEMA if entry.name == raw[0]), None
        )
        if item is None:
            error(context.console, f"未知配置项: {raw[0]}")
            return
        if len(raw) == 2:
            _write(context, item, raw[1])
        else:
            _edit(context, item, context.runtime.config.display(item.name))
        return

    while True:
        values = context.runtime.config.display_all()
        # 不打标题: 分类头已经写着"界面 日志 执行 模型", 再加一行"配置"是复述用户刚
        # 敲的那条命令.
        picked = choose_grouped(context.console, _groups(values))
        if picked is None:
            return
        if picked.key == "__model__":
            model_commands.cmd_model(context, "")
        elif picked.key == "__provider__":
            provider_commands.cmd_provider(context, "")
        elif picked.key == "__gateway__":
            model_commands.cmd_gateway(context, "")
        else:
            item = next(
                entry for entry in config_keys.SCHEMA if entry.name == picked.key
            )
            _edit(context, item, values[item.name])


def _groups(values: dict[str, str]) -> list[Group]:
    """按键名前缀分类. 认不出前缀的归"其他", 而不是消失."""
    buckets: dict[str, list[Option]] = {label: [] for _, label in _CATEGORIES}
    buckets[_OTHER] = []
    labels = dict(_CATEGORIES)
    for entry in config_keys.SCHEMA:
        prefix = entry.name.partition(".")[0]
        buckets[labels.get(prefix, _OTHER)].append(
            Option(entry.name, entry.title, values[entry.name])
        )
    buckets[_JUMP_CATEGORY].extend(
        Option(key, label, hint) for key, label, hint in _JUMPS
    )
    ordered = [label for _, label in _CATEGORIES] + [_OTHER]
    return [Group(label, tuple(buckets[label])) for label in ordered if buckets[label]]


def _edit(context: CommandContext, item: ConfigKey, current: str) -> None:
    """改一项. 说明在这里打 —— 列表里每项都带一段多行说明会把菜单顶出屏幕."""
    console = context.console
    console.print(Text(f"{item.title} ({item.name})", style=STYLE_DIM))
    console.print(Text(item.help, style=STYLE_DIM))
    if item.kind is ValueKind.BOOL:
        _write(
            context,
            item,
            "true"
            if confirm(console, item.title, default=current == "true")
            else "false",
        )
        return
    if item.kind is ValueKind.CHOICE:
        picked = choose(
            console, [Option(value, value) for value in item.choices], current=current
        )
        if picked is not None:
            _write(context, item, picked.key)
        return
    value = ask_text(console, item.title, default=current)
    if value is not None:
        _write(context, item, value)


def _write(context: CommandContext, item: ConfigKey, value: str) -> None:
    try:
        context.runtime.config.set(item.name, value)
    except ConfigValidationError as exc:
        error(context.console, str(exc))
        return
    ok(context.console, f"{item.title} = {context.runtime.config.display(item.name)}")
