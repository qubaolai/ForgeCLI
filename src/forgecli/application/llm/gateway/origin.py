"""调用用途枚举 RequestOrigin（ADR-0011 §3.3 / §3.4）。

origin 表示一次模型调用的用途，取自封闭枚举，不绑定任何第三方编排概念。
它是 §3.4「用途 -> 选择策略」映射表的左列，新增用途必须先扩这个枚举。

谁来定 origin：由 AgentTurnService / AgentLoop 根据用户输入、slash command、
mode policy 裁决（ADR-0011 §5 步骤 1），LLM 不能自行指定。gateway 只按 origin 路由，
不决定 origin。

到选择策略的默认映射（§3.4，由后续 SelectionResolver 切片落地，本日不实现路由）：
    chat / act / tool_observation -> current_model
    final_summary                 -> current_model
    title / summary               -> tier: fast
    compact                       -> tier: fast（不足时升 smart）
    plan / review / debug         -> tier: smart
    structured_classification     -> tier: fast
"""

from __future__ import annotations

from enum import Enum


class RequestOrigin(Enum):
    """模型调用用途。枚举值与 ADR-0011 §3.4 映射表一一对应。"""

    CHAT = "chat"
    ACT = "act"
    TOOL_OBSERVATION = "tool_observation"  # 与 tool calling 一同推迟实现，枚举值先冻
    FINAL_SUMMARY = "final_summary"
    TITLE = "title"
    SUMMARY = "summary"
    COMPACT = "compact"
    PLAN = "plan"
    REVIEW = "review"
    DEBUG = "debug"
    STRUCTURED_CLASSIFICATION = "structured_classification"
