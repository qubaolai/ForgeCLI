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


# 哪些用途**不需要**思考 (ADR-0011 §3.3 明写 origin 用于"参数默认").
#
# 判据是这次调用要不要**推理**: 起标题, 压缩上下文, 做摘要与结构化分类, 都是把已经在
# 上下文里的东西换个形状, 推理预算花在那里是纯浪费. 实测一次 `act` 调用花了 5,569 个
# 思考 token 去决定把待办第 5 项打勾 —— 那一类才是要治的, 只是 origin 分不出来 (见下).
#
# **穷尽覆盖**, 不用默认分支 (ADR-0040 决策 9): 新增一个用途时, 遗漏会让测试红, 而不是
# 悄悄按"要思考"处理. 有一条用例专查这张表覆盖了枚举的每一个成员.
#
# 已知上限: `act` 是循环里唯一的用途, 而它既包含"读三个文件再决定改哪里", 也包含"把
# 待办打个勾". origin 是调用发出**之前**就定好的标签, 分不出这两者 —— 所以这张表治不了
# 循环内的浪费, 那要么靠调低模型自身的 effort, 要么需要一个 origin 表达不了的判据.
_THINKING_BY_ORIGIN: dict[RequestOrigin, bool] = {}


def _register_thinking_defaults() -> None:
    thinks = {
        RequestOrigin.CHAT,
        RequestOrigin.ACT,
        RequestOrigin.PLAN,
        RequestOrigin.REVIEW,
        RequestOrigin.DEBUG,
        RequestOrigin.TOOL_OBSERVATION,
    }
    mechanical = {
        RequestOrigin.TITLE,
        RequestOrigin.SUMMARY,
        RequestOrigin.FINAL_SUMMARY,
        RequestOrigin.COMPACT,
        RequestOrigin.STRUCTURED_CLASSIFICATION,
    }
    missing = set(RequestOrigin) - thinks - mechanical
    if missing:
        raise ValueError(f"新增用途未归类: {sorted(item.value for item in missing)}")
    _THINKING_BY_ORIGIN.update(dict.fromkeys(thinks, True))
    _THINKING_BY_ORIGIN.update(dict.fromkeys(mechanical, False))


_register_thinking_defaults()


def benefits_from_thinking(origin: RequestOrigin) -> bool:
    """这个用途值不值得花思考预算."""
    return _THINKING_BY_ORIGIN[origin]
