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

from rich.padding import Padding
from rich.text import Text

from forgecli.application.config.config_service import ConfigValueView
from forgecli.domain.config import config_keys
from forgecli.domain.config.config_keys import ConfigKey, ConfigLevel, ValueKind
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
from forgecli.interfaces.tui.console import (
    STYLE_ACCENT,
    STYLE_DIM,
    error,
    kv_table,
    ok,
)
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

_LEVEL_LABELS = {
    ConfigLevel.APP: "应用级 · config.json",
    ConfigLevel.PROJECT: "项目级 · forge.json",
}


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
            if raw[1] == "--reset":
                _reset(context, item)
            else:
                _write(context, item, raw[1])
        else:
            _edit(context, _view_for(context, item))
        return

    while True:
        views = _value_views(context)
        # 不打标题: 分类头已经写着"界面 日志 执行 模型", 再加一行"配置"是复述用户刚
        # 敲的那条命令.
        picked = choose_grouped(context.console, _groups(views))
        if picked is None:
            return
        if picked.key == "__model__":
            model_commands.cmd_model(context, "")
        elif picked.key == "__provider__":
            provider_commands.cmd_provider(context, "")
        elif picked.key == "__gateway__":
            model_commands.cmd_gateway(context, "")
        else:
            _edit(context, views[picked.key])


def _groups(values: dict[str, str] | dict[str, ConfigValueView]) -> list[Group]:
    """按键名前缀分类. 认不出前缀的归"其他", 而不是消失."""
    buckets: dict[str, list[Option]] = {label: [] for _, label in _CATEGORIES}
    buckets[_OTHER] = []
    labels = dict(_CATEGORIES)
    for entry in config_keys.SCHEMA:
        prefix = entry.name.partition(".")[0]
        raw = values[entry.name]
        if isinstance(raw, ConfigValueView):
            value = _shown_value(raw.value)
            source = "已覆盖" if raw.overridden else "默认"
            hint = f"{value}  ·  {source}  ·  {_short_level(entry.level)}"
        else:
            # 兼容只提供有效值的轻量调用方；生产路径始终传 ConfigValueView。
            hint = _shown_value(raw)
        buckets[labels.get(prefix, _OTHER)].append(
            Option(entry.name, entry.title, hint)
        )
    buckets[_JUMP_CATEGORY].extend(
        Option(key, label, hint) for key, label, hint in _JUMPS
    )
    ordered = [label for _, label in _CATEGORIES] + [_OTHER]
    return [Group(label, tuple(buckets[label])) for label in ordered if buckets[label]]


def _edit(context: CommandContext, view: ConfigValueView) -> None:
    """配置详情与动作。值、来源、作用域、生效时机都在提交前可见。"""
    console = context.console
    item = view.key
    console.print()
    console.print(Text(item.title, style=f"bold {STYLE_ACCENT}"))
    console.print(
        Padding(
            kv_table(
                (
                    ("键", item.name),
                    ("当前值", _shown_value(view.value)),
                    ("来源", "用户覆盖" if view.overridden else "SCHEMA 默认值"),
                    ("作用域", _LEVEL_LABELS[item.level]),
                    ("生效", item.effect),
                )
            ),
            (0, 0, 0, 2),
        )
    )
    console.print(Text(f"  {item.help}", style=STYLE_DIM))

    actions = [Option("edit", "修改值", _value_hint(item))]
    if item.allow_empty and view.value:
        actions.append(Option("empty", "设为空", "使用该配置定义的空值语义"))
    if view.overridden:
        actions.append(Option("reset", "恢复默认", f"→ {_shown_value(item.default)}"))
    picked = choose(console, actions)
    if picked is None:
        return
    if picked.key == "reset":
        _reset(context, item)
        return
    if picked.key == "empty":
        _write(context, item, "")
        return

    current = view.value
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
    value = ask_text(console, item.title, default=current, allow_empty=item.allow_empty)
    if value is not None:
        _write(context, item, value)


def _write(context: CommandContext, item: ConfigKey, value: str) -> None:
    try:
        context.runtime.config.set(item.name, value)
    except ConfigValidationError as exc:
        error(context.console, str(exc))
        return
    ok(
        context.console,
        f"{item.title} = {_shown_value(context.runtime.config.display(item.name))}"
        f"  ·  {item.effect}",
    )


def _reset(context: CommandContext, item: ConfigKey) -> None:
    try:
        context.runtime.config.unset(item.name)
    except ConfigValidationError as exc:
        error(context.console, str(exc))
        return
    ok(
        context.console,
        f"{item.title} 已恢复默认 = {_shown_value(item.default)}  ·  {item.effect}",
    )


def _value_views(context: CommandContext) -> dict[str, ConfigValueView]:
    service = context.runtime.config
    # value_views 是应用服务的新契约。保留这个窄回退，让文档示例和测试里的轻量替身不必
    # 为了画菜单实现整套配置服务；真实运行时不会走这里。
    if hasattr(service, "value_views"):
        return {view.key.name: view for view in service.value_views()}
    values = service.display_all()
    return {
        item.name: ConfigValueView(item, values[item.name], False)
        for item in config_keys.SCHEMA
    }


def _view_for(context: CommandContext, item: ConfigKey) -> ConfigValueView:
    return _value_views(context)[item.name]


def _shown_value(value: str) -> str:
    return value if value else "（空）"


def _short_level(level: ConfigLevel) -> str:
    return "应用" if level is ConfigLevel.APP else "项目"


def _value_hint(item: ConfigKey) -> str:
    if item.kind is ValueKind.BOOL:
        return "是 / 否"
    if item.kind is ValueKind.CHOICE:
        return " / ".join(item.choices)
    if item.kind is ValueKind.INT:
        return "非负整数"
    return "文本"
