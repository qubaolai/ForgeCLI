"""mode 能力门: 决定模型能看见哪些工具 (ADR-0013 §1, ADR-0004 §7).

能力门在**目录**这一层生效, 不在工具内部, 也不在注册表里: plan 档下写文件, 编辑,
删除, Shell 和脚本工具根本不进 ToolCatalog, 模型看不见就不会请求.

模型绕过目录直接请求目录外工具时, 协调器返回 tool_unavailable_in_mode. 那只是防御性
兜底, 不能替代"从目录里移除工具"这件事本身.
"""

from __future__ import annotations

from forgecli.domain.intents import SessionMode
from forgecli.domain.tool.capability import MUTATING_CAPABILITIES
from forgecli.domain.tool.catalog import CatalogQuery
from forgecli.domain.tool.spec import ToolSpec

__all__ = ["catalog_query_for_mode"]


def catalog_query_for_mode(mode: SessionMode) -> CatalogQuery:
    """构造该模式下的目录查询.

    只有 plan 档收窄目录. 其余三档的差别体现在裁决 (mode 能力预算 + 规则), 而不是
    "看不看得见" —— accept_edits 下让模型看不见 shell_run, 它只会改用别的方式绕,
    不如让它请求, 然后在裁决层给出明确的 ASK.
    """
    if mode is SessionMode.PLAN:
        return CatalogQuery(predicate=_plan_mode_visible, reason_tag="mode:plan")
    return CatalogQuery(predicate=_always_visible, reason_tag=f"mode:{mode.value}")


def _plan_mode_visible(spec: ToolSpec) -> bool:
    """plan 档只保留 PLAN_ONLY 与只读工具.

    看的是**能力上界**而不是单次调用: 一个上界里带 WORKSPACE_WRITE 的工具, 即使这次
    只想读, 也不该出现在 plan 档的目录里.
    """
    return not (spec.declared_capabilities & MUTATING_CAPABILITIES)


def _always_visible(spec: ToolSpec) -> bool:
    return True
