"""origin 枚举完整性：与 ADR-0011 §3.4 映射表一一对应（11 个用途）。"""

from __future__ import annotations

from forgecli.application.llm.gateway import RequestOrigin

# §3.4 映射表左列的全部用途值。新增用途必须先扩枚举，本测试随之更新。
_EXPECTED_VALUES = {
    "chat",
    "act",
    "tool_observation",
    "final_summary",
    "title",
    "summary",
    "compact",
    "plan",
    "review",
    "debug",
    "structured_classification",
}


def test_origin_covers_exactly_the_adr_mapping_table() -> None:
    assert {o.value for o in RequestOrigin} == _EXPECTED_VALUES
    assert len(list(RequestOrigin)) == len(_EXPECTED_VALUES)


def test_final_summary_is_a_frozen_member() -> None:
    # 任务收尾的最终总结用途；运行时触发在 AgentLoop 切片接线，今日只冻枚举值。
    assert RequestOrigin.FINAL_SUMMARY.value == "final_summary"


def test_values_are_unique() -> None:
    values = [o.value for o in RequestOrigin]
    assert len(values) == len(set(values))
