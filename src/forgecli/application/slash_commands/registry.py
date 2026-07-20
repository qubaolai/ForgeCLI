"""Slash command registry.

CommandRegistry 是会话内斜杠命令目录：命令名 -> CommandSpec，服务两个使用方：
    - IntentRouter：命令名是否已注册，决定解析成 SlashCommand 还是 UnknownCommand。
    - REPL 分派器：取 spec.handler 执行；是否记录会话事件由 handler 的返回值
      （是否真正发生持久化写入）决定，而非命令的静态分类。

新增命令时注册一条 spec 即可，IntentRouter 与 REPL 分派逻辑不需要随之改动。
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.application.slash_commands.base import CommandHandler


@dataclass(frozen=True)
class CommandSpec:
    """一条斜杠命令的注册信息，描述路由与分派所需的最小元数据。"""

    name: str  # 规范的命令名（小写）
    summary: str = ""  # 供 /help 命令展示使用
    handler: CommandHandler | None = None  # 执行该命令的处理器（必填）

    def __post_init__(self) -> None:
        # 构造即有效：每条命令都必须能被执行。
        if self.handler is None:
            raise ValueError(f"斜杠命令缺少 handler: {self.name}")


class CommandRegistry:
    """命令名 -> CommandSpec 注册表"""

    def __init__(self) -> None:
        self._specs: dict[str, CommandSpec] = {}

    def register(self, spec: CommandSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"命令重复注册: {spec.name}")
        self._specs[spec.name] = spec

    def register_all(self, specs: list[CommandSpec]) -> None:
        for spec in specs:
            self.register(spec)

    def get(self, name: str) -> CommandSpec | None:
        return self._specs.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def all_specs(self) -> tuple[CommandSpec, ...]:
        """按命令名排序返回全部 spec，供 /help 渲染。"""
        return tuple(self._specs[name] for name in sorted(self._specs))
