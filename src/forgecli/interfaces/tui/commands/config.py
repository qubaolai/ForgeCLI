"""/config: 常规配置.

条目, 中文名, 说明, 取值范围全部来自 ``domain/config/config_keys`` 的 SCHEMA. 这里
一个字面量都不写: 同一个开关在终端和网页上叫不同的名字, 两边都不会报错.
"""

from __future__ import annotations

from rich.text import Text

from forgecli.domain.config import config_keys
from forgecli.domain.config.config_keys import ConfigKey, ValueKind
from forgecli.interfaces.tui.chooser import Option, ask_text, choose, confirm
from forgecli.interfaces.tui.commands.context import CommandContext
from forgecli.interfaces.tui.console import (
    STYLE_DIM,
    error,
    listing,
    ok,
    rule,
)
from forgecli.shared.errors import ConfigValidationError

_LEVEL_LABELS = {"APP": "应用级", "PROJECT": "项目级"}


def cmd_config(context: CommandContext, argument: str) -> None:
    if not context.require_idle():
        return
    runtime = context.runtime
    values = runtime.config.display_all()
    raw = argument.strip().split(maxsplit=1)
    if raw:
        key = raw[0]
        item = next((entry for entry in config_keys.SCHEMA if entry.name == key), None)
        if item is None:
            error(context.console, f"未知配置项: {key}")
            return
        if len(raw) == 2:
            _write(context, item, raw[1])
        else:
            _edit(context, item, values[item.name])
        return

    rule(context.console, "常规配置")
    context.console.print(
        listing(
            ("配置项", "当前值", "归属", "说明"),
            (
                (
                    f"{entry.title}\n{entry.name}",
                    values[entry.name],
                    _LEVEL_LABELS.get(entry.level.name, entry.level.name),
                    entry.help,
                )
                for entry in config_keys.SCHEMA
            ),
        )
    )
    picked = choose(
        context.console,
        "改哪一项",
        [
            Option(entry.name, entry.title, values[entry.name])
            for entry in config_keys.SCHEMA
        ],
    )
    if picked is None:
        return
    item = next(entry for entry in config_keys.SCHEMA if entry.name == picked.key)
    _edit(context, item, values[item.name])


def _edit(context: CommandContext, item: ConfigKey, current: str) -> None:
    console = context.console
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
            console,
            item.title,
            [Option(value, value) for value in item.choices],
            current=current,
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
