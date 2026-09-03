"""计划评审: 一轮停下来等人拍板时走这里 (ADR-0022, ADR-0023).

四个选项的语义由 ``PlanReviewChoice`` 固定, 界面不解释 —— 终端把"同意并执行"说成
"批准", 用户就不会知道它会顺带升档 (ADR-0038).
"""

from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from forgecli.application.planning.plan_review import PlanReviewChoice
from forgecli.interfaces.runtime.project_runtime import ProjectRuntime
from forgecli.interfaces.tui.chooser import Option, ask_text, choose
from forgecli.interfaces.tui.console import STYLE_DIM, ok, rule, warn

_CHOICES = (
    Option("approve_and_run", "同意并执行", "批准并立刻按待办开工; 计划档会升到 auto"),
    Option("approve", "只批准", "批准并建好待办, 什么时候开工由你说"),
    Option("amend", "补充意见", "把意见给模型, 让它重出一版"),
    Option("reject", "拒绝", "这份作废, 下一句话由你说"),
)


def review(console: Console, runtime: ProjectRuntime) -> bool:
    """展示待评审的计划并读一个裁决. 返回 False 表示用户没有做决定."""
    active = runtime.tools.planning.load()
    plan = active.plan
    if plan is None:
        warn(console, "这一轮说要评审计划, 但计划文件里没有待评审的那一份")
        return False
    rule(console, f"计划评审 · {plan.title}")
    markdown = runtime.tools.planning.read_plan()
    if markdown:
        console.print(Markdown(markdown))
    console.print(Text(f"plan_id={plan.plan_id} · r{plan.revision}", style=STYLE_DIM))
    console.print()
    picked = choose(console, "怎么处理这份计划", _CHOICES)
    if picked is None:
        warn(console, "计划还挂着; 想继续时再用 /plan review")
        return False
    note = ""
    if picked.key == "amend":
        note = ask_text(console, "补充意见") or ""
        if not note:
            return False
    outcome = runtime.resolve_plan_review(PlanReviewChoice(picked.key), note)
    if outcome is None:
        warn(console, "当前没有待评审的计划")
        return False
    ok(console, outcome.message)
    return True
