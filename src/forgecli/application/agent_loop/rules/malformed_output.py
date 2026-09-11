"""模型的工具调用本身不可用: 参数不是完整 JSON, 或参数里混进了工具调用标记.

见过一次真实任务里 search_text 的唯一一次调用就这么被打掉, 而模型收不到任何反馈, 从此
再没碰过这个工具, 全程改用 shell_run —— 一次静默的格式失败足以让一个工具从模型的选项
里永久消失.

绝不猜一个参数补上去, 那等于替模型编参数. 但"不替它补"和"不告诉它"是两件事: 告诉它
坏在哪, 它自己能改. 这里只做后者.

与打转那几条规则的分工: 那些拦"同一件事重复做", "换着花样撞安全策略", "调用没带回新
信息", 输入是合法调用; 这一条拦的是"话都说不利索", 输入根本不是一个可派发的调用.
"""

from __future__ import annotations

from forgecli.application.agent_loop.model_invoker import ModelOutcome
from forgecli.application.agent_loop.rule import (
    AfterModelVerdict,
    LoopView,
    ModelErrorVerdict,
)
from forgecli.application.agent_loop.transcript import protocol_markup_in
from forgecli.application.agent_loop.verdicts import Continue, Reask
from forgecli.application.llm.gateway.errors import (
    MalformedToolCallError,
    ModelGatewayError,
)
from forgecli.application.prompt.template_renderer import render_notice
from forgecli.domain.agent.actions import LoopStop
from forgecli.domain.agent.stop import LoopStopReason
from forgecli.domain.conversation.message import ChatMessage, TextBlock
from forgecli.domain.conversation.turn import MessageRole
from forgecli.shared.observability.log import get_log

__all__ = ["MAX_MALFORMED_RESPONSES", "MalformedOutputRule"]

_log = get_log(__name__)

# 本轮累计收到多少次不可用的工具调用之后放弃.
#
# 数**累计**不数连续: 中间夹一次成功调用就重置的话, 一个每隔一步坏一次的模型能把整轮
# 预算烧光而永远撞不到上限.
MAX_MALFORMED_RESPONSES = 2


class MalformedOutputRule:
    def __init__(self) -> None:
        self._count = 0

    def after_model(self, outcome: ModelOutcome, view: LoopView) -> AfterModelVerdict:
        """参数值或工具名里混进了标记.

        这类调用不能派发下去: 它会一路走到安全裁决, 拿回 executable_not_found 之类的
        结论, 把模型引向"我命令写错了" —— 真正坏掉的是它的输出格式.
        """
        for call in outcome.tool_calls:
            marker = protocol_markup_in(call)
            if marker is None:
                continue
            _log.warning(
                "model.protocol_markup",
                tool=call.name,
                marker=marker,
                arguments=call.arguments,
            )
            return self._retry(
                render_notice("loop.malformed_detail", tool=call.name, marker=marker)
            )
        return Continue()

    def on_bad_json(
        self, error: ModelGatewayError, view: LoopView
    ) -> ModelErrorVerdict:
        """参数不是完整 JSON (流式半截, 或非流式解析失败)."""
        if not isinstance(error, MalformedToolCallError):
            return Continue()
        return self._retry(str(error))

    def _retry(self, detail: str) -> Reask | LoopStop:
        """告诉模型坏在哪, 再给一次机会.

        不收工具目录: 模型没有做错事, 只是话没说利索, 收掉目录等于因为口吃罚它闭嘴.
        """
        self._count += 1
        _log.warning(
            "model.malformed_tool_call",
            detail=detail,
            count=self._count,
            limit=MAX_MALFORMED_RESPONSES,
        )
        if self._count > MAX_MALFORMED_RESPONSES:
            return LoopStop(
                LoopStopReason.MODEL_ERROR_BLOCKING,
                render_notice("stop.malformed", count=self._count),
            )
        notice = render_notice("loop.malformed", detail=detail)
        return Reask(
            messages=(
                ChatMessage(role=MessageRole.USER, content=(TextBlock(notice),)),
            ),
            summary=f"工具调用格式损坏, 重试: {detail}",
        )
