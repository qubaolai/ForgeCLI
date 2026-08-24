"""计划的固定模板与渲染 (ADR-0022 §3).

**模型给字段, Forge 给格式.** 模板不是为了好看, 而是让计划可比较, 可测试, 可以在不同
模型之间保持一致 —— 一份每次结构都不一样的计划, 人读起来要重新找一遍章节, 程序则根本
无从校验它写没写验收标准.

因此"格式随意发挥"在入参层面就不可表达: `plan.write` 收的是结构化字段, 渲染只在这里。

TOML 是权威格式, Markdown 是派生物, 单向. 反向解析 Markdown 会把模板变成一门需要 parser
的语言, 而它的全部价值恰恰在于不需要.

本文件**不**并入 domain/prompt/text.py (ADR-0031 决策 2): 它是 PlanDocument 的渲染器,
正文由模型给, 这里只给格式. 改动任何一段模板文本, 都要同时升 PLAN_TEMPLATE_VERSION
并更新快照测试 ——
与 PROMPT_TEXT_VERSION 同一条规矩.
"""

from __future__ import annotations

from forgecli.domain.planning import PlanDocument, PlanStep

__all__ = ["PLAN_TEMPLATE_VERSION", "render_plan"]

PLAN_TEMPLATE_VERSION = 1

# 空小节的占位. 保留标题而不是删掉整节: 删掉会让不同计划的结构不一致, 也会让人误以为
# 模板变了.
_EMPTY = "(无)"


def render_plan(plan: PlanDocument) -> str:
    """把结构化计划渲染成人与模型都读这一份的 Markdown.

    小节与顺序固定. `plan.read` 返回的就是这份正文 —— 模型看到的与人看到的完全一致,
    否则"你批准的那份"和"模型以为的那份"就成了两个东西.
    """
    sections = [
        f"# {plan.title.strip()}",
        _section("目标", plan.goal),
        _section("现状与背景", plan.context),
        _section("方案", plan.approach),
        _section("步骤", _steps(plan.steps)),
        _section("风险", _bullets(plan.risks)),
        _section("验收标准", _bullets(plan.acceptance)),
    ]
    return "\n\n".join(sections) + "\n"


def _section(heading: str, body: str) -> str:
    return f"## {heading}\n\n{body.strip() or _EMPTY}"


def _steps(steps: tuple[PlanStep, ...]) -> str:
    """编号列表, 一条一项 —— 这一节就是待办骨架 (ADR-0022 决策 3).

    编号从 1 起, 因为这是给人读的正文. 待办清单的序号从 0 起, 与 todo.set_status 的入参
    一致 —— 两套编号服务于两类读者, 不强行统一.
    """
    if not steps:
        return _EMPTY
    lines: list[str] = []
    for number, step in enumerate(steps, start=1):
        lines.append(f"{number}. {step.title.strip()}")
        detail = step.detail.strip()
        if detail:
            lines.extend(f"   {line}" for line in detail.splitlines())
    return "\n".join(lines)


def _bullets(items: tuple[str, ...]) -> str:
    kept = [item.strip() for item in items if item.strip()]
    if not kept:
        return _EMPTY
    return "\n".join(f"- {item}" for item in kept)
