"""/diagnostics: 当前进程的日志位置与运行读数 (ADR-0035).

排查一次异常行为的顺序基本固定: 先知道日志在哪, 再看这一段时间里哪一类调用慢了或者
在报错, 然后才带着 turn_id 去翻日志. 这条命令负责前两步.

`--reset` 把计数与耗时清零. 用途是"从现在开始重新量一次": 一次会话跑了两小时之后,
累计数字反映的是整段历史, 而想优化的往往只是刚刚那一次慢调用.
"""

from __future__ import annotations

from forgecli.application.interaction_ports import UserOutput
from forgecli.application.llm.gateway.observability import InProcessGatewayMetrics
from forgecli.application.slash_commands.base import CommandHandler
from forgecli.domain.intents import SlashCommand
from forgecli.interfaces.runtime.diagnostics import (
    diagnostics_report,
    render_diagnostics,
)
from forgecli.shared.observability.metrics import METRICS

__all__ = ["DiagnosticsCommand"]


class DiagnosticsCommand(CommandHandler):
    """展示日志装配, 阶段耗时与模型调用读数."""

    def __init__(
        self,
        output: UserOutput,
        gateway_metrics: InProcessGatewayMetrics | None = None,
    ) -> None:
        self._output = output
        self._gateway_metrics = gateway_metrics

    def execute(self, command: SlashCommand) -> bool:
        if "--reset" in command.args:
            METRICS.reset()
            self._output.print("已清空阶段计数与耗时 (日志文件不受影响).")
            return False
        self._output.print(
            render_diagnostics(diagnostics_report(self._gateway_metrics))
        )
        return False  # 纯查看, 不写任何持久状态
