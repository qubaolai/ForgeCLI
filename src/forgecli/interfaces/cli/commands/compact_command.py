"""/compact: 手动压缩当前会话历史 (ADR-0032 决策 9).

自动压缩由预算触发, 只在快撑满时才动手. 手动这条是给"我知道接下来要干一件大事"准备的:
用户可以在开始之前先腾出空间, 而不是等跑到一半被压缩打断.

走的是与自动压缩同一个 ContextManager 与同一条落盘路径, 所以 /resume 重建 transcript
时分不出这一次是谁触发的 —— 也不需要分.
"""

from __future__ import annotations

from forgecli.application.agent_turn.agent_turn_service import AgentTurnService
from forgecli.application.interaction_ports import UserOutput
from forgecli.application.slash_commands.base import CommandHandler
from forgecli.domain.intents import SlashCommand

__all__ = ["CompactCommand"]


class CompactCommand(CommandHandler):
    def __init__(self, agent_turn: AgentTurnService, output: UserOutput) -> None:
        self._agent_turn = agent_turn
        self._output = output

    def execute(self, command: SlashCommand) -> bool:
        saved = self._agent_turn.compact()
        if saved <= 0:
            # 说清是"没压动"而不是"压缩失败": 历史本来就短的时候这是正常结果, 而
            # "失败"会让用户以为要去查什么.
            self._output.print("当前历史无需压缩, 或没有可压缩的部分.")
        else:
            self._output.print(f"已压缩历史, 约省下 {saved} token.")
        # 压缩不改配置.
        return False
