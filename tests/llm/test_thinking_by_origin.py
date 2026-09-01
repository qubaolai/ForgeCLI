"""思考预算按调用用途分档 (ADR-0011 §3.3 明写 origin 用于"参数默认").

起标题, 压缩上下文, 做摘要与结构化分类, 都是把已经在上下文里的东西换个形状. 它们与真正
要推理的调用共用同一个模型, 只按模型配就只能一起开或一起关.

**已知上限**: 实测日志里 172 次调用有 170 次是 `act` —— 循环里唯一的用途. 而 `act` 既
包含"读三个文件再决定改哪里", 也包含"把待办第 5 项打勾"(那一次花了 5,569 个思考 token).
origin 是调用发出**之前**定好的标签, 分不出这两者, 所以这张表治不了循环内的浪费.
"""

from __future__ import annotations

from forgecli.domain.model.origin import RequestOrigin, benefits_from_thinking


def test_every_origin_is_classified() -> None:
    """穷尽覆盖 (ADR-0040 决策 9).

    新增用途时遗漏要让用例红, 而不是悄悄按"要思考"处理.
    """
    for origin in RequestOrigin:
        assert isinstance(benefits_from_thinking(origin), bool)


def test_mechanical_uses_do_not_think() -> None:
    """它们不推理, 只是把已有内容换个形状."""
    assert not benefits_from_thinking(RequestOrigin.COMPACT)
    assert not benefits_from_thinking(RequestOrigin.TITLE)
    assert not benefits_from_thinking(RequestOrigin.SUMMARY)
    assert not benefits_from_thinking(RequestOrigin.FINAL_SUMMARY)
    assert not benefits_from_thinking(RequestOrigin.STRUCTURED_CLASSIFICATION)


def test_reasoning_uses_keep_their_budget() -> None:
    assert benefits_from_thinking(RequestOrigin.ACT)
    assert benefits_from_thinking(RequestOrigin.CHAT)
    assert benefits_from_thinking(RequestOrigin.PLAN)
    assert benefits_from_thinking(RequestOrigin.REVIEW)
