"""计划评审: 四选一的语义与状态迁移 (ADR-0023 决策 3 / 4, 决策 4 由 ADR-0038 修订).

**它不驱动界面.** 读一行输入, 弹一个菜单, 打印一段文字 —— 那些是 CLI 的事. 这里只回答
"用户选了这一项之后, 计划与待办各自变成什么, 以及要不要再起一轮".

评审不走 ApprovalService, 因此这里**不产生 ExecutionAuthorization, 不进 RiskCache, 不写
learned rules**. 人同意一份计划, 不会让计划里提到的任何一条命令在下次自动放行.

### 换档是这个模块唯一碰权限的地方

"同意并执行"把 PLAN 换回进 plan 之前的那一档 (`pre_plan_mode`, ADR-0055 决策 1; 没有就
ACCEPT_EDITS), 约束写死在 `_upgraded_mode` 里:

1. 仅当当前档是 PLAN 时换档.
2. 绝不换到 BYPASS_PERMISSIONS, 无论计划正文写了什么 —— 计划是模型产出的低信任文本,
   不能成为提权的依据; 那一档只能由人自己切.
3. **批准计划不等于批准计划里的任何一次工具调用.** 换档之后每一步执行仍然逐次经过
   完整裁决 (ADR-0055 决策 4). 换档与在 plan 档手敲 /mode 完全等价, 不多一分.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from forgecli.core.permissions import PermissionMode
from forgecli.planning.plan import PlanDocument, PlanStatus
from forgecli.planning.service import PlanningService

__all__ = ["PlanReviewChoice", "PlanReviewOutcome", "PlanReviewService"]


class PlanReviewChoice(Enum):
    """四选一. 语义固定, 不由界面解释."""

    AMEND = "amend"
    REJECT = "reject"
    APPROVE = "approve"
    APPROVE_AND_RUN = "approve_and_run"


@dataclass(frozen=True)
class PlanReviewOutcome:
    """裁决之后发生了什么. CLI 据此决定打印什么, 以及要不要再起一轮."""

    choice: PlanReviewChoice
    plan: PlanDocument | None = None
    # 非空表示要以这段文字再起一轮. 空表示本次到此为止.
    follow_up: str = ""
    # 升档后的模式; None 表示不改档.
    upgraded_mode: PermissionMode | None = None
    seeded_todo_count: int = 0
    message: str = ""


# "同意并执行"的合成输入. 它是**程序**给的, 因此再入必须用 InputOrigin.PROGRAM ——
# 绕回 Repl._process_line 会把它按 TTY 用户输入解析, 而一段以 `#` 开头的文字就会变成一次
# 不受裁决的 Shell (ADR-0023 决策 5).
_RUN_PROMPT = "计划已批准. 按待办清单从第一步开始执行, 每完成一步就更新它的状态."

_AMEND_PROMPT = "用户对上一份计划提出了补充意见:\n\n{note}\n\n请据此提交新的一版计划."


class PlanReviewService:
    def __init__(self, planning: PlanningService) -> None:
        self._planning = planning

    def decide(
        self,
        choice: PlanReviewChoice,
        plan: PlanDocument,
        *,
        mode: PermissionMode,
        pre_plan_mode: PermissionMode | None = None,
        note: str = "",
    ) -> PlanReviewOutcome:
        if choice is PlanReviewChoice.REJECT:
            # 刻意不起新一轮: 花一次模型调用去说"不行"是浪费, 而用户拒绝之后下一句话本来
            # 就该由他自己说 —— 与 _weigh 对人类拒绝的既有处理逻辑一致.
            rejected = self._planning.set_plan_status(plan.plan_id, PlanStatus.REJECTED)
            # plan 一定要带上: 拒绝恰恰是最该留痕的一次裁决, 而 outcome 是调用方唯一
            # 拿得到"人裁了哪一份"的地方.
            return PlanReviewOutcome(
                choice=choice,
                plan=rejected or plan,
                message="计划已拒绝. 告诉我你想怎么改, 或者换个方向.",
            )

        if choice is PlanReviewChoice.AMEND:
            # plan_id 不变: 一次"补充 -> 重提"的往返留在同一份计划的历史里, 而不是散成
            # 两份互不相干的计划.
            superseded = self._planning.set_plan_status(
                plan.plan_id, PlanStatus.SUPERSEDED
            )
            return PlanReviewOutcome(
                choice=choice,
                plan=superseded or plan,
                follow_up=_AMEND_PROMPT.format(note=note.strip()),
                message="已记下补充意见, 正在重新拟定.",
            )

        approved = self._planning.set_plan_status(plan.plan_id, PlanStatus.APPROVED)
        # 批准一份计划 = 用它的步骤播种待办 (ADR-0022 决策 3). 这一步让"计划到执行"
        # 之间不需要人再翻译一次.
        todo = self._planning.seed_from_plan(approved or plan)
        if choice is PlanReviewChoice.APPROVE:
            # 不起新一轮: 让用户可以先批准, 再决定什么时候开工.
            return PlanReviewOutcome(
                choice=choice,
                plan=approved,
                seeded_todo_count=len(todo.items),
                message=f"计划已批准, 待办已按 {len(todo.items)} 个步骤建好.",
            )

        upgraded = _upgraded_mode(mode, pre_plan_mode)
        return PlanReviewOutcome(
            choice=choice,
            plan=approved,
            follow_up=_RUN_PROMPT,
            upgraded_mode=upgraded,
            seeded_todo_count=len(todo.items),
            message=(
                f"计划已批准, 待办已按 {len(todo.items)} 个步骤建好."
                + (f" 模式已切到 {upgraded.value}." if upgraded is not None else "")
            ),
        )


def _upgraded_mode(
    mode: PermissionMode, pre_plan_mode: PermissionMode | None
) -> PermissionMode | None:
    """唯一允许的换档: PLAN -> 进 plan 之前的那一档 (ADR-0055 决策 1).

    不看计划内容: 计划是模型产出的低信任文本, "计划里写了什么能不能影响档位"这个问题
    在类型层面就没有入口. 记不得进 plan 之前是哪一档时落到 ACCEPT_EDITS —— "同意并
    执行"表达的是"按这份计划自己做完", 停在每一次编辑都要问的 DEFAULT 名实不符.
    BYPASS_PERMISSIONS 仍在这条边之外: 那种决定只能由人自己做.

    在其他档批准一份计划不改档: 既不升也不降.
    """
    if mode is not PermissionMode.PLAN:
        return None
    if pre_plan_mode is None or pre_plan_mode is PermissionMode.BYPASS_PERMISSIONS:
        return PermissionMode.ACCEPT_EDITS
    return pre_plan_mode
