"""mode 能力门: 决定模型能看见哪些工具 (ADR-0013 §1, ADR-0004 §7).

能力门在**目录**这一层生效, 不在工具内部, 也不在注册表里: 只读隔离档下写文件, 编辑,
删除, Shell 和脚本工具根本不进 ToolCatalog, 模型看不见就不会请求.

模型绕过目录直接请求目录外工具时, 协调器返回 tool_unavailable_in_mode. 那只是防御性
兜底, 不能替代"从目录里移除工具"这件事本身.
"""

from __future__ import annotations

from forgecli.domain.intents import SandboxLevel, SessionMode
from forgecli.domain.tool.capability import MUTATING_CAPABILITIES
from forgecli.domain.tool.catalog import CatalogQuery
from forgecli.domain.tool.spec import ToolSpec

__all__ = ["catalog_query_for_mode"]


def catalog_query_for_mode(mode: SessionMode) -> CatalogQuery:
    """构造该模式下的目录查询.

    收窄的判据是**隔离档**而不是整个模式: 工作区一个字节都写不进去的时候, 摆一个写工具
    在目录里只会让模型请求一次注定失败的调用. 审批档不参与 —— 让模型看不见 shell_run
    并不能阻止它绕, 不如让它请求, 然后在裁决层给出明确的 ASK.
    """
    if mode.sandbox is SandboxLevel.READ_ONLY:
        return CatalogQuery(
            predicate=_read_only_visible, reason_tag=f"sandbox:{mode.sandbox.value}"
        )
    return CatalogQuery(predicate=_always_visible, reason_tag=f"mode:{mode.value}")


def _read_only_visible(spec: ToolSpec) -> bool:
    """只读隔离档下只保留 PLAN_ONLY 与只读工具.

    看的是**能力上界**而不是单次调用: 一个上界里带 WORKSPACE_WRITE 的工具, 即使这次
    只想读, 也不该出现在 plan 档的目录里.
    """
    return not (spec.declared_capabilities & MUTATING_CAPABILITIES)


def _always_visible(spec: ToolSpec) -> bool:
    return True
    # return spec.name == "shell_run"
