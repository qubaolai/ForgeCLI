"""/tools: 当前模式下模型能看见哪些工具, 各自的能力上界是什么 (ADR-0007).

回答的是一个具体问题: **"我现在这档下, Agent 能干什么"**. 所以它按当前 mode 走与模型
完全相同的目录谓词 (catalog_query_for_mode), 而不是把注册表整个列出来 —— 后者会让
plan 档看起来也能写文件.

被当前模式挡掉的工具单独列一节而不是直接不显示: "看不见 fs.edit_file" 与
"没有 fs.apply_patch 这个工具" 是两回事, 混在一起用户会以为功能缺失.

展示的是 spec 里的**能力上界**, 不是某次调用的事实. 上界宽不等于这次调用危险, 反过来
也一样 —— 真正的裁决依据是每次调用的 ToolPlan, 这里给不出, 也不该假装给得出.
"""

from __future__ import annotations

from forgecli.application.interaction_ports import UserOutput
from forgecli.application.session import SessionService
from forgecli.application.slash_commands import CommandHandler
from forgecli.application.tool_request.catalog_predicates import catalog_query_for_mode
from forgecli.application.tools.registry import ToolRegistry
from forgecli.domain.intents import SlashCommand
from forgecli.domain.tool.catalog import CatalogQuery
from forgecli.domain.tool.spec import ToolSpec

__all__ = ["ToolsCommand"]

_NAME_WIDTH = 16


class ToolsCommand(CommandHandler):
    def __init__(
        self,
        registry: ToolRegistry,
        session: SessionService,
        output: UserOutput,
    ) -> None:
        self._registry = registry
        self._session = session
        self._output = output

    def execute(self, command: SlashCommand) -> bool:
        mode = self._session.current().mode
        visible = self._registry.list(catalog_query_for_mode(mode))
        everything = self._registry.list(CatalogQuery(lambda _: True, "all"))
        hidden = tuple(
            spec for spec in everything.entries if spec.name not in visible.names
        )

        snapshot = visible.catalog_snapshot_hash[:12]
        self._output.print(f"模式 {mode.value} · 目录快照 {snapshot}")
        self._output.print(f"\n模型可见 ({len(visible.entries)}):")
        for spec in visible.entries:
            self._output.print(_row(spec))
        if hidden:
            self._output.print(f"\n本模式下不可见 ({len(hidden)}):")
            for spec in hidden:
                self._output.print(_row(spec))
        self._output.print(
            "\n能力是 spec 声明的上界, 不是本次调用的事实; 是否放行按每次调用逐次裁决."
        )
        return False  # 纯查看


def _row(spec: ToolSpec) -> str:
    capabilities = ", ".join(sorted(item.value for item in spec.declared_capabilities))
    return (
        f"  {spec.name:<{_NAME_WIDTH}} v{spec.version:<5}"
        f" 目标声明 {spec.target_declaration_ability.value:<10} {capabilities}"
    )
