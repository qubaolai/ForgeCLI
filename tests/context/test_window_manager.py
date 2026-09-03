"""撞水位才淘汰, 淘汰之后剩下什么 (ADR-0041 决策 5 / 决策 7).

上一份用例查的是 `Window` 与 `WindowPolicy` 这两个值对象自己的判断; 这一份查的是把它们
串起来的那条真实通路 —— 估算, 比水位, 挑切点, 叫模型摘要, 记草稿.
"""

from __future__ import annotations

from collections.abc import Iterator

from forgecli.application.context.window_manager import WindowManager
from forgecli.domain.context.budget import ContextBudget
from forgecli.domain.context.window import Window
from forgecli.domain.conversation.message import ChatMessage, TextBlock
from forgecli.domain.conversation.turn import MessageRole
from forgecli.domain.model.request import ModelRequest, StructuredModelRequest
from forgecli.domain.model.response import (
    FinishReason,
    ModelResponse,
    ModelUsage,
    StructuredModelResponse,
)
from forgecli.domain.model.streaming import ModelStreamChunk

# 8 KiB 的内联上限, 与 ArtifactPolicy 的缺省一致.
_MAX_INLINE = 8 * 1024


class _Summariser:
    """只会写交接说明的网关替身. 记下它被调过几次与收到了什么."""

    def __init__(self, text: str = "早前做过的事: 改了 a.py") -> None:
        self.text = text
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            request_id=request.request_id,
            provider="fake",
            model="fake-1",
            content=self.text,
            finish_reason=FinishReason.STOP,
            usage=ModelUsage(input_tokens=100, output_tokens=10),
            latency_ms=1.0,
        )

    def stream(self, request: ModelRequest) -> Iterator[ModelStreamChunk]:
        raise NotImplementedError("摘要走非流式")

    def complete_structured(
        self, request: StructuredModelRequest
    ) -> StructuredModelResponse:
        raise NotImplementedError("摘要不走结构化输出")


def _user(text: str) -> ChatMessage:
    return ChatMessage(role=MessageRole.USER, content=(TextBlock(text),))


def _long_window(count: int, chars: int = 1_200) -> Window:
    """一段足够撑破水位的窗口. 每条都是用户消息, 所以切点处处合法."""
    return Window(
        messages=tuple(_user(f"第{i}条 " + "x" * chars) for i in range(count))
    )


def _budget(effective: int) -> ContextBudget:
    return ContextBudget(
        context_window=200_000,
        reserved_output_tokens=0,
        effective_context_tokens=effective,
    )


def _fit(manager: WindowManager, window: Window, budget: ContextBudget | None):
    return manager.fit(
        window, budget=budget, session_id="s1", turn_id="t1", system_prompt="策略"
    )


# ---- 什么时候淘汰 ----


def test_nothing_happens_below_the_high_water() -> None:
    """没撞水位就一个字节都不动.

    这是最要紧的一条: 窗口没满时动它永远是净亏 —— 省下几千 token, 换后面几万从缓存价变
    全价. 淘汰的定位是溢出保护, 不是成本控制.
    """
    gateway = _Summariser()
    manager = WindowManager(max_inline_bytes=_MAX_INLINE, gateway=gateway)
    window = _long_window(4)

    result = _fit(manager, window, _budget(64_000))

    assert result.window is window
    assert result.drafts == ()
    assert gateway.requests == [], "没撞水位就不该叫模型"


def test_no_budget_means_no_eviction() -> None:
    """没给预算就什么都不做, 不猜一个窗口大小.

    猜小了平白丢内容, 猜大了等于没有这道防线.
    """
    manager = WindowManager(max_inline_bytes=_MAX_INLINE, gateway=_Summariser())
    window = _long_window(50)

    result = _fit(manager, window, None)

    assert result.window is window
    assert result.estimated_input == 0


def test_crossing_the_high_water_evicts_once() -> None:
    """撞了就淘汰, 而且只淘汰**一次** —— 批量, 不是逐条滑动."""
    gateway = _Summariser()
    manager = WindowManager(max_inline_bytes=_MAX_INLINE, gateway=gateway)
    window = _long_window(40)

    result = _fit(manager, window, _budget(4_000))

    assert len(result.window.messages) < len(window.messages)
    assert len(result.drafts) == 1
    assert len(gateway.requests) == 1, "一次淘汰只叫一次模型"


def test_eviction_lands_below_the_high_water() -> None:
    """淘汰完必须真的降到高水位以下.

    回归: 逐字保留那一支原先没有上限, 于是一段全是用户消息的窗口"淘汰"完比淘汰前还大
    (实测高水位 4000, 淘汰后 16212) —— 而且下一步立刻又撞上, 每一轮都在作废前缀.
    """
    manager = WindowManager(max_inline_bytes=_MAX_INLINE, gateway=_Summariser())
    budget = _budget(4_000)

    result = _fit(manager, _long_window(40), budget)

    assert result.estimated_input < budget.request_high_water


def test_a_second_eviction_does_not_compound_the_verbatim_section() -> None:
    """上一份交接说明不再被逐字抄一遍 (回归).

    交接说明走的是 user 角色, 所以它长得和用户原话一样. 不跳过的话, 它里面已经保留过一次
    的原话会被再抄一遍, 下次再抄一遍 —— 每淘汰一次翻一倍, 而淘汰正是为了变小.
    """
    manager = WindowManager(max_inline_bytes=_MAX_INLINE, gateway=_Summariser())
    budget = _budget(4_000)

    once = _fit(manager, _long_window(40), budget)
    twice = _fit(manager, once.window.append(*_long_window(40).messages), budget)

    assert (
        twice.estimated_input <= once.estimated_input * 2
    ), "第二次淘汰之后不该比第一次大出一个量级"


def test_forced_eviction_does_not_blow_up_on_a_tiny_budget() -> None:
    """`force=True` 无条件淘汰一次, 而不是抛异常 (回归).

    供应商回过"上下文太长"之后, 循环靠这条路救回这一轮. 原先它是把预算的 effective 压到
    1 来逼淘汰, 而那让两条水位一起归零 —— `WindowPolicy` 要求低水位严格小于高水位, 于是
    本该救命的路径自己抛 ValueError, 被上层宽 except 吞成 turn FAILED.
    """
    manager = WindowManager(max_inline_bytes=_MAX_INLINE, gateway=_Summariser())
    window = _long_window(40)

    result = manager.fit(
        window,
        budget=_budget(200_000),  # 高水位远在窗口之上: 不 force 的话什么都不会发生
        session_id="s1",
        turn_id="t1",
        system_prompt="策略",
        force=True,
    )

    assert len(result.window.messages) < len(window.messages)
    assert len(result.drafts) == 1


def test_without_force_the_same_budget_evicts_nothing() -> None:
    """反向用例: 没有它, 上面那条断言在"根本没走 force"时也会绿."""
    manager = WindowManager(max_inline_bytes=_MAX_INLINE, gateway=_Summariser())
    window = _long_window(40)

    result = _fit(manager, window, _budget(200_000))

    assert result.window is window


# ---- 淘汰之后剩下什么 ----


def test_the_handover_note_becomes_the_first_message() -> None:
    """交接说明是淘汰后窗口的第一条, 不是插在中间."""
    gateway = _Summariser("早前: 已经改完 a.py, 用例还没跑")
    manager = WindowManager(max_inline_bytes=_MAX_INLINE, gateway=gateway)

    result = _fit(manager, _long_window(40), _budget(4_000))

    head = result.window.messages[0]
    assert head.role is MessageRole.USER
    assert "早前: 已经改完 a.py, 用例还没跑" in head.content[0].text  # type: ignore[union-attr]


def test_the_dropped_user_messages_survive_verbatim() -> None:
    """用户原话逐字保留 (ADR-0041 决策 5).

    模型的话丢了是损失, 用户的话丢了是错误 —— 那是这次任务的目标与约束本身.
    """
    manager = WindowManager(max_inline_bytes=_MAX_INLINE, gateway=_Summariser())
    window = Window(
        messages=(
            _user("目标: 给 offset 加越界校验"),
            *_long_window(40).messages,
        )
    )

    result = _fit(manager, window, _budget(4_000))

    rendered = "\n".join(
        block.text
        for message in result.window.messages
        for block in message.content
        if isinstance(block, TextBlock)
    )
    assert "目标: 给 offset 加越界校验" in rendered


def test_eviction_still_happens_without_a_gateway() -> None:
    """没接网关也照样淘汰, 只是没有交接说明.

    窗口撞了高水位, 不淘汰这一轮就发不出去 —— 让它因为缺一个可选协作件而卡住是最坏的
    结果. 用户原话那一支不依赖模型.
    """
    manager = WindowManager(max_inline_bytes=_MAX_INLINE)

    result = _fit(manager, _long_window(40), _budget(4_000))

    assert len(result.window.messages) < 40
    assert result.drafts[0].summary == ""


def test_the_eviction_is_metered() -> None:
    """淘汰那次模型调用要记账 (ADR-0037).

    它的 input 大致等于被丢掉的那一段 —— 不是零头. 不记的话本轮合计对不上账单.
    """
    manager = WindowManager(max_inline_bytes=_MAX_INLINE, gateway=_Summariser())

    result = _fit(manager, _long_window(40), _budget(4_000))

    # 没接 UsageMeter 时不产出草稿, 但省下多少这一侧照样记.
    assert result.usage_drafts == ()
    assert result.drafts[0].tokens_before > result.drafts[0].tokens_after


def test_a_window_with_no_legal_split_is_reported_not_forced() -> None:
    """挑不出切点时如实说, 不硬切.

    从 tool call 配对中间切开, 供应商会拒掉整个请求 —— 而这时窗口已经吃紧, 再失败一次
    这一轮就彻底没救了.
    """
    manager = WindowManager(max_inline_bytes=_MAX_INLINE, gateway=_Summariser())
    window = Window(messages=(_user("x" * 40_000),))

    result = _fit(manager, window, _budget(1_000))

    assert result.window is window
    assert result.drafts == ()
    assert result.over_allowance is False, "没超硬限就不该报 over_allowance"


def test_a_too_tight_policy_is_logged_not_raised(records) -> None:  # type: ignore[no-untyped-def]
    """水位小到装不下一条满额结果时, 说出原因但不杀掉这一轮 (ADR-0041 决策 8).

    抛出去等于让一个偏紧的配置直接判死一轮, 而淘汰在那种配置下仍然有用 —— 只是保证不了
    一定放得下. 日志的价值是: 用户后来报"它老说压不下去"时, 这一行直接指出是哪两个数
    撞上了.
    """
    manager = WindowManager(max_inline_bytes=_MAX_INLINE, gateway=_Summariser())

    _fit(manager, _long_window(40), _budget(4_000))

    assert any(
        "window.policy_too_tight" in record.message for record in records.records
    )
