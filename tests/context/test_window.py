"""窗口只追加, 批量水位淘汰 (ADR-0041 决策 4 / 决策 5 / 决策 8).

这一组查的是**什么时候淘汰, 淘汰之后剩下什么**. 两件事错了都不报错: 淘汰太频繁只是每轮
变贵, 淘汰丢错了东西只是模型忽然不知道自己在干嘛.
"""

from __future__ import annotations

import pytest

from forgecli.application.context.transcript import safe_split_points
from forgecli.domain.context.budget import ContextBudget
from forgecli.domain.context.window import Window, WindowPolicy
from forgecli.domain.conversation.message import ChatMessage, TextBlock, ToolResultBlock
from forgecli.domain.conversation.turn import MessageRole
from forgecli.domain.tool.tool_call import ToolCall


def _user(text: str) -> ChatMessage:
    return ChatMessage(role=MessageRole.USER, content=(TextBlock(text),))


def _assistant(text: str) -> ChatMessage:
    return ChatMessage(role=MessageRole.ASSISTANT, content=(TextBlock(text),))


def _call(call_id: str) -> ChatMessage:
    return ChatMessage(
        role=MessageRole.ASSISTANT,
        content=(TextBlock("我看一下"),),
        tool_calls=(ToolCall(tool_call_id=call_id, name="fs_read", arguments={}),),
    )


def _result(call_id: str) -> ChatMessage:
    return ChatMessage(
        role=MessageRole.TOOL,
        content=(ToolResultBlock(tool_call_id=call_id, content="读到了"),),
    )


# ---- 水位是导出的, 不是常量 ----


def test_an_unknown_effective_length_falls_back_to_the_hard_limit() -> None:
    """目录里没填有效长度时, 水位只按硬限算 —— 也就是原来那套单阈值设计."""
    budget = ContextBudget(context_window=100_000, reserved_output_tokens=0)
    assert budget.request_high_water == 80_000


def test_a_known_effective_length_wins_when_it_is_smaller() -> None:
    """两个上限取更小的那个.

    标称 200k 的模型按比例算会得出 160k 的高水位, 而那远超它能可靠工作的长度.
    """
    budget = ContextBudget(
        context_window=200_000,
        reserved_output_tokens=0,
        effective_context_tokens=64_000,
    )
    assert budget.request_high_water == 64_000


def test_the_same_transcript_gets_different_watermarks_per_model() -> None:
    """水位跟着模型走, 不是写死的常量.

    这一条挡的是"把 30k 直接写进代码": 那样换个小窗口模型就再也不会淘汰, 而它不报错.
    """
    big = ContextBudget(context_window=200_000, effective_context_tokens=64_000)
    small = ContextBudget(context_window=32_000, effective_context_tokens=32_000)
    assert big.request_high_water > small.request_high_water


def test_the_prefix_is_deducted_from_the_window_share() -> None:
    """水位管整条请求, 窗口的份额是减出来的.

    工具目录, 静态策略与运行上下文同样占位置. 只按窗口自己算水位, 等于把前缀那几千
    token 漏掉, 于是每一轮都比该淘汰的时候晚一点淘汰.
    """
    budget = ContextBudget(context_window=100_000)
    assert budget.window_allowance(5_000) == budget.request_high_water - 5_000
    # 前缀比水位还大时给 0, 不给负数.
    assert budget.window_allowance(10_000_000) == 0


# ---- 构建期不变量 ----


def test_a_window_too_small_for_one_full_result_fails_at_construction() -> None:
    """一条满额工具结果装不下时, 构建期就失败 (ADR-0041 决策 8).

    这是真正无解的那条线: 淘汰之后窗口里必然还留着最近那一条, 连它自己都超过低水位的话,
    淘汰多少次都没用. 让它在构建期炸, 而不是等运行期撞上"淘汰完还是放不下" —— 那时候
    这一轮已经没救了.
    """
    policy = WindowPolicy(high_water_tokens=5_000, low_water_tokens=2_000)
    with pytest.raises(ValueError, match="调小 max_inline_bytes"):
        policy.assert_fits(max_inline_bytes=8 * 1024)


def test_a_roomy_window_passes_the_invariant() -> None:
    """一个正常的 64k 模型不该被这条拦下.

    反向用例: 判据定得太严, 表现出来就是一条不该响的警报, 而那比没有警报更糟 ——
    下一个人会把它关掉.
    """
    policy = WindowPolicy(high_water_tokens=33_000, low_water_tokens=13_000)
    policy.assert_fits(max_inline_bytes=8 * 1024)


def test_the_low_water_must_sit_below_the_high_water() -> None:
    with pytest.raises(ValueError, match="low_water_tokens"):
        WindowPolicy(high_water_tokens=100, low_water_tokens=100)


# ---- 淘汰 ----


def test_eviction_never_splits_a_tool_call_from_its_result() -> None:
    """从 tool call 配对中间切开, 供应商会拒掉整个请求.

    而这次失败发生在窗口已经吃紧的时候 —— 再失败一次, 这一轮就彻底没救了.
    """
    window = Window(
        messages=(
            _user("改一下"),
            _call("c1"),
            _result("c1"),
            _call("c2"),
            # c2 的结果还欠着: 切在这里就是把配对劈开.
        )
    )
    plan = window.plan_eviction(
        split_points=safe_split_points(window.messages),
        keep_last=1,
        verbatim_chars=10_000,
    )
    assert plan.split_index in safe_split_points(window.messages)
    assert plan.split_index <= 3


def test_eviction_keeps_the_most_recent_messages() -> None:
    """留的是最近的: 模型正在做的那件事全在这几条里."""
    window = Window(messages=tuple(_user(f"第 {i} 句") for i in range(10)))
    plan = window.plan_eviction(
        split_points=safe_split_points(window.messages),
        keep_last=3,
        verbatim_chars=10_000,
    )
    assert len(plan.kept) >= 3
    assert plan.kept[-1] == window.messages[-1]


def test_eviction_preserves_user_messages_verbatim() -> None:
    """用户原话逐字保留 (ADR-0041 决策 5).

    模型的话丢了是损失, 用户的话丢了是错误 —— 那是这次任务的目标与约束本身. 它便宜:
    一次会话十几条, 占窗口不到 2%.
    """
    window = Window(
        messages=(
            _user("给 offset 加越界校验"),
            _assistant("好"),
            _user("顺便别动 limit"),
            _assistant("明白"),
            _user("现在改"),
        )
    )
    plan = window.plan_eviction(
        split_points=safe_split_points(window.messages),
        keep_last=1,
        verbatim_chars=10_000,
    )
    kept_text = [
        block.text
        for message in plan.preserved_user_messages
        for block in message.content
        if isinstance(block, TextBlock)
    ]
    assert "给 offset 加越界校验" in kept_text
    assert "顺便别动 limit" in kept_text


def test_eviction_picks_the_latest_legal_split() -> None:
    """靠后的切点意味着这次多丢一些, 下一次淘汰离得更远.

    淘汰次数才是代价: 每一次都作废一整个前缀. 挑最靠前的切点会让淘汰变得频繁, 而那正是
    决策 5 要避免的滑动窗口.
    """
    window = Window(messages=tuple(_user(f"第 {i} 句") for i in range(10)))
    plan = window.plan_eviction(
        split_points=safe_split_points(window.messages),
        keep_last=2,
        verbatim_chars=10_000,
    )
    assert plan.split_index == 8


def test_a_window_with_nothing_to_drop_reports_empty() -> None:
    """挑不出切点时如实说, 不假装淘汰过."""
    window = Window(messages=(_user("你好"),))
    plan = window.plan_eviction(
        split_points=safe_split_points(window.messages),
        keep_last=6,
        verbatim_chars=10_000,
    )
    assert plan.empty
    assert plan.kept == window.messages


# ---- 只追加 ----


def test_the_window_only_appends() -> None:
    """append 不改已有的那几条.

    在前缀缓存下, 改一处的成本等于它自己加上它后面的全部内容 —— 实测一次中段改写让
    57% 的输入退出缓存.
    """
    window = Window(messages=(_user("一"), _user("二")))
    grown = window.append(_user("三"))
    assert grown.messages[:2] == window.messages
    assert window.messages == (_user("一"), _user("二"))


def test_the_rewrite_entry_point_is_gone() -> None:
    """`transcript` 只导出一个函数.

    留着 `rewrite` 就等于留着退回去的路 —— "回头整理一下历史"永远是看起来合理的.
    """
    from forgecli.application.context import transcript

    assert transcript.__all__ == ["safe_split_points"]
    assert not hasattr(transcript, "rewrite")
    assert not hasattr(transcript, "slots_of")
