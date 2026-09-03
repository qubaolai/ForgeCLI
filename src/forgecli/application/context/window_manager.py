"""窗口维护: 只追加, 撞高水位时整体淘汰一次 (ADR-0041 决策 4 / 决策 5 / 决策 7).

替代了原先的 `ContextManager`. 那一版有三条通路 (去重, 降级, 摘要), 前两条都是**中段
改写** —— 在前缀缓存下每一次都在净亏. 删掉之后剩下的只有这一件事: 估一下, 超了就淘汰.

不依赖 `LlmGateway` 之外的任何东西, 也不写事件: 循环可以调模型 (它本来就持有网关),
但不能写事件. 所以这里只产出 `CompactionDraft` 草稿, 由 `AgentTurnService` 统一落盘 ——
与 `UsageRecordDraft` 完全同构.
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.application.context import transcript
from forgecli.application.context.summarize import summarize
from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.application.llm.gateway.token_estimator import ApproximateTokenEstimator
from forgecli.application.llm.metering import UsageMeter
from forgecli.domain.context.budget import ContextBudget
from forgecli.domain.context.compaction import CompactionDraft, CompactionLevel
from forgecli.domain.context.window import Window, WindowPolicy
from forgecli.domain.model.usage import UsageRecordDraft
from forgecli.domain.tool.tool_call import ToolSchema
from forgecli.shared.observability.log import get_log

__all__ = ["WindowFitResult", "WindowManager"]


_log = get_log(__name__)

# 与 ApproximateTokenEstimator 的拉丁常数一致. 只用来把 token 额度折回字符数.
_CHARS_PER_TOKEN = 3


@dataclass(frozen=True)
class WindowFitResult:
    """一次整理的产出."""

    window: Window
    drafts: tuple[CompactionDraft, ...] = ()
    # 摘要那次模型调用的计量草稿 (ADR-0037). 与 drafts 分开: 一个说"省下多少上下文",
    # 一个说"这次淘汰花了多少 token", 是方向相反的两个数.
    usage_drafts: tuple[UsageRecordDraft, ...] = ()
    estimated_input: int = 0
    # 淘汰完仍然放不下. 循环据此走 CONTEXT_COMPACTION_REQUIRED, 而不是把一个必然被网关
    # 拒掉的请求发出去.
    over_allowance: bool = False


class WindowManager:
    """无状态. 每次从传进来的窗口重新算."""

    def __init__(
        self,
        *,
        max_inline_bytes: int,
        gateway: LlmGateway | None = None,
        estimator: ApproximateTokenEstimator | None = None,
        meter: UsageMeter | None = None,
    ) -> None:
        # 单条工具结果的内联上限, 由装配层从 ResourceGovernor 取. 必填而不是给个缺省:
        # 缺省值与真实配置对不上的话, 那条水位判据查的就是另一套数, 而它不会报错.
        self._max_inline_bytes = max_inline_bytes
        # gateway 缺省为 None: 没接时照常淘汰, 只是被丢掉那一段没有交接说明 ——
        # 用户原话仍然逐字保留, 那一支不依赖模型.
        self._gateway = gateway
        self._estimator = estimator or ApproximateTokenEstimator()
        # meter 缺省为 None: 没接计量时摘要照常跑, 只是这次调用不产出计量草稿.
        self._meter = meter

    def fit(
        self,
        window: Window,
        *,
        budget: ContextBudget | None,
        session_id: str,
        turn_id: str,
        system_prompt: str = "",
        tools: tuple[ToolSchema, ...] = (),
        force: bool = False,
    ) -> WindowFitResult:
        """调模型之前整理一次窗口.

        ``force`` 跳过水位判断, 无条件淘汰一次. 只有一个调用方: 供应商已经回过"上下文
        太长", 那就是放不下, 估算说什么都不作数了.

        用一个显式参数而不是让调用方临时改预算: 改预算的那条路会把两条水位一起压到 1,
        而 ``WindowPolicy`` 要求低水位严格小于高水位 —— 于是本该救回这一轮的降级路径
        自己抛 ValueError.
        """
        if budget is None:
            # 没给预算就什么都不做. 不猜一个窗口大小 —— 猜小了平白丢内容, 猜大了等于
            # 没有这道防线.
            return WindowFitResult(window=window)

        prefix = self._estimator.estimate_text(system_prompt)
        prefix += self._tools_tokens(tools)
        estimated = prefix + self._messages_tokens(window)
        if not force and estimated <= budget.request_high_water:
            _log.debug(
                "window.within_budget",
                estimated_input=estimated,
                high_water=budget.request_high_water,
            )
            return WindowFitResult(window=window, estimated_input=estimated)

        policy = _policy_for(budget, prefix)
        self._warn_if_too_tight(policy)
        return self._evict(
            window,
            policy=policy,
            budget=budget,
            prefix=prefix,
            before=estimated,
            session_id=session_id,
            turn_id=turn_id,
        )

    def _evict(
        self,
        window: Window,
        *,
        policy: WindowPolicy,
        budget: ContextBudget,
        prefix: int,
        before: int,
        session_id: str,
        turn_id: str,
    ) -> WindowFitResult:
        """一次性淘汰到低水位.

        切点在合法位置里挑最靠后的那一个 —— 靠后意味着这次多丢一些, 下一次淘汰离得更远,
        而淘汰次数才是代价: 每一次都作废一整个前缀.
        """
        plan = window.plan_eviction(
            split_points=transcript.safe_split_points(window.messages),
            keep_last=policy.min_messages,
            # 逐字保留的额度取低水位的一小份: 它是为了不丢目标与约束, 不是为了把历史
            # 整段搬过去. 给多了, 一段全是用户消息的窗口淘汰完会比淘汰前还大.
            verbatim_chars=policy.low_water_tokens * _CHARS_PER_TOKEN // 4,
        )
        if plan.empty:
            # 连一个合法切点都挑不出来 (整段欠着 tool result, 或者本来就没几条).
            _log.warning("window.eviction_impossible", messages=len(window.messages))
            return WindowFitResult(
                window=window,
                estimated_input=before,
                over_allowance=before > budget.allowance,
            )

        if self._gateway is None:
            messages = plan.kept
            summary = None
        else:
            messages, summary = summarize(
                plan,
                self._gateway,
                session_id=session_id,
                turn_id=turn_id,
                meter=self._meter,
            )

        evicted = Window(
            messages=messages,
            evicted_count=window.evicted_count + len(plan.dropped),
        )
        after = prefix + self._messages_tokens(evicted)
        _log.info(
            "window.evicted",
            dropped=len(plan.dropped),
            kept=len(plan.kept),
            tokens_before=before,
            tokens_after=after,
            summarised=summary is not None and bool(summary.text),
        )
        draft = CompactionDraft(
            level=CompactionLevel.SUMMARY,
            tokens_before=before,
            tokens_after=after,
            messages_replaced=len(plan.dropped),
            summary="" if summary is None else summary.text,
            provider="" if summary is None else summary.provider,
            model="" if summary is None else summary.model,
        )
        usage = () if summary is None or summary.usage is None else (summary.usage,)
        return WindowFitResult(
            window=evicted,
            drafts=(draft,),
            usage_drafts=usage,
            estimated_input=after,
            over_allowance=after > budget.allowance,
        )

    def _warn_if_too_tight(self, policy: WindowPolicy) -> None:
        """水位小到装不下一条满额结果时, 把原因说出来 (ADR-0041 决策 8).

        **只记不抛.** 这确实是配错了 —— 淘汰之后窗口里必然还留着最近那一条, 连它自己
        都超过低水位的话, 淘汰多少次都不会够. 但抛出去等于让一个"偏紧"的配置直接杀掉
        这一轮, 而淘汰在那种配置下仍然有用, 只是保证不了一定放得下.

        日志的价值在这里: 用户后来报"它老说上下文压不下去"时, 这一行直接指出是哪两个数
        撞上了, 而不用去猜.
        """
        try:
            policy.assert_fits(max_inline_bytes=self._max_inline_bytes)
        except ValueError as error:
            _log.error(
                "window.policy_too_tight",
                message=str(error),
                low_water=policy.low_water_tokens,
                max_inline_bytes=self._max_inline_bytes,
            )

    def _messages_tokens(self, window: Window) -> int:
        return self._estimator.estimate_input(messages=window.messages)

    def _tools_tokens(self, tools: tuple[ToolSchema, ...]) -> int:
        return self._estimator.estimate_input(messages=(), tools=tools)


def _policy_for(budget: ContextBudget, prefix: int) -> WindowPolicy:
    """把预算扣掉前缀之后折成两条水位.

    低水位先算, 高水位再取"至少比它大一"的那个: ``WindowPolicy`` 要求严格小于, 而前缀
    大到吃掉整个额度时两者会一起归零. 那种配置确实有问题 (由 `_warn_if_too_tight` 说出
    来), 但它不该让构造本身抛异常 —— 抛在这里等于把一个偏紧的配置变成一次崩溃.
    """
    low = max(1, budget.window_floor(prefix))
    high = max(low + 1, budget.window_allowance(prefix))
    return WindowPolicy(high_water_tokens=high, low_water_tokens=low)
