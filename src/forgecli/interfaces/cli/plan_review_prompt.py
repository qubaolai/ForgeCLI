"""计划评审的终端交互 (ADR-0023 §1 / §3 / §4).

它只做三件事: 打印计划正文, 让用户四选一, 把选择交回 PlanReviewService. 状态迁移与升档
规则一概不在这里 —— 那些是 application 的事, 放进界面等于让"用户看到什么"和"系统做什么"
各有一份真相.

**位置约束**: 必须在本轮的 Rich `Live` 上下文退出之后驱动. 同一个 console 上两个 Live
会打架 —— run_renderer 已经为审批场景专门做过挂起. 评审走的是回合之后, 因此不需要挂起,
但顺序不能反.

**非交互环境**: 没有前台 TTY 时无人可裁决. 计划照常落盘并保持 proposed, 本轮结束并给出
可行动提示, **不自动批准** (ADR-0023 §3). 失效方向朝保守.

**中断不是裁决**: 菜单期间 Ctrl-C / Esc 等于"什么都不做", 计划保持 proposed. 把中断当成
拒绝, 用户手一抖就把一份计划判了死刑, 而他本来只是想看看清楚.
"""

from __future__ import annotations

from rich.console import Console

from forgecli.application.planning import (
    PlanningService,
    PlanReviewChoice,
    PlanReviewOutcome,
    PlanReviewService,
)
from forgecli.domain.intents import SessionMode
from forgecli.interfaces.cli.tty.select import (
    SelectOption,
    SelectUnavailable,
    select_one,
)

__all__ = ["PlanReviewPrompt"]

_OPTIONS = (
    SelectOption(
        key=PlanReviewChoice.APPROVE_AND_RUN.value,
        label="同意并执行",
        detail="批准计划, 建好待办, 并立刻开始做 (plan 档会切到 accept_edits)",
    ),
    SelectOption(
        key=PlanReviewChoice.APPROVE.value,
        label="同意",
        detail="批准计划并建好待办, 但先不开工",
    ),
    SelectOption(
        key=PlanReviewChoice.AMEND.value,
        label="补充",
        detail="给一句补充意见, 让它重新拟一版",
    ),
    SelectOption(
        key=PlanReviewChoice.REJECT.value,
        label="拒绝",
        detail="作废这份计划",
    ),
)

_NO_TTY_NOTICE = (
    "当前不在终端中, 无法评审计划. 计划已保存并保持待裁决状态, "
    "回到交互式会话后用 /plan show 查看."
)

_CANCELLED_NOTICE = "已跳过评审. 计划仍在等你裁决, 用 /plan show 可以再看一遍."


class PlanReviewPrompt:
    """驱动一次计划评审. 返回 None 表示没有裁决发生."""

    def __init__(
        self,
        console: Console,
        planning: PlanningService,
        review: PlanReviewService,
    ) -> None:
        self._console = console
        self._planning = planning
        self._review = review

    def run(self, mode: SessionMode) -> PlanReviewOutcome | None:
        active = self._planning.load()
        plan = active.plan
        if plan is None:
            # 停下来等评审, 却读不到计划. 不该发生, 但真发生时说清楚比静默回到提示符好.
            self._console.print("没有找到待评审的计划.")
            return None

        body = self._planning.read_plan(plan.plan_id) or ""
        self._console.print()
        for line in body.splitlines():
            self._console.print(line)
        self._console.print()

        try:
            chosen = select_one(self._console, list(_OPTIONS))
        except SelectUnavailable:
            self._console.print(_NO_TTY_NOTICE)
            return None
        if chosen is None:
            self._console.print(_CANCELLED_NOTICE)
            return None

        note = ""
        if chosen.key == PlanReviewChoice.AMEND.value:
            note = self._read_note()
            if not note:
                # 补充意见是这条路径唯一的输入. 拿不到就没有可提交的东西, 计划保持原状.
                self._console.print(_CANCELLED_NOTICE)
                return None

        outcome = self._review.decide(
            PlanReviewChoice(chosen.key), plan, mode=mode, note=note
        )
        if outcome.message:
            self._console.print(outcome.message)
        return outcome

    def _read_note(self) -> str:
        self._console.print("补充意见 (直接回车放弃):")
        try:
            return input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""
