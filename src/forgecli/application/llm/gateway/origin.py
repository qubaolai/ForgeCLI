"""调用用途枚举 RequestOrigin（ADR-0011 §3.3 / §3.4）。

origin 表示一次模型调用的*用途*，取自封闭枚举，不绑定任何第三方编排概念。

origin 只是用途标签：用于提示词组装、参数默认、usage / 审计记录和限流分类。它**不选择
模型**，也不映射任何模型档位——系统不维护 fast / smart 之类档位（ADR-0011 §决策）。未被
显式覆盖的用途一律走当前主模型；可选的按用途显式覆盖由 ModelSelectionResolver 读取
（§3.4，后续切片落地）。动作权限、工具与审批属于 AgentTurnService 的 mode policy，也
不参与模型选择。

谁来定 origin：由 AgentTurnService / AgentLoop 根据用户输入、slash command、
mode policy 裁决（ADR-0011 §5 步骤 1），LLM 不能自行指定。gateway 只按 origin 归类，
不决定 origin，也不因 origin 切换模型。
"""

from __future__ import annotations

from enum import Enum


class RequestOrigin(Enum):
    """模型调用用途。新增用途必须先扩这个封闭枚举（ADR-0011 §3.3）。"""

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
