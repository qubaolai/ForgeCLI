"""ContextManager: 上下文管理对外的唯一入口 (ADR-0032 决策 1).

循环在每次模型调用之前调它一次. 分工按 ADR-0010: 循环可以调模型 (它本来就持有
``LlmGateway``), 但**不能写事件** —— 所以这里只产出 ``CompactionDraft``, 由
``AgentTurnService`` 统一落盘, 与 ``UsageRecordDraft`` 完全同构.

预算是传进来的纯值对象, 不在这里查模型目录: ``SIBLING_BANS`` 禁了
``application.agent_loop -> application.llm.gateway.default_gateway``, 让循环持有一个
会去查目录的组件, 等于绕开那条禁令.

## 顺序

1. **去重与变更通知** (决策 3 / 4) —— 无条件跑. 它不是为了省空间, 是为了让模型知道
   "这份你读过"和"这个文件后来被改了". 预算再宽也要跑.
2. 估算. 没超阈值就到此为止.
3. **一级降级** (决策 2) —— 确定性, 不花钱, 3 天内可逆.
4. 还超就 **二级摘要** —— 不可复现, 所以放在最后.
5. 还超就如实报告 ``over_allowance``, 由循环按 ``CONTEXT_COMPACTION_REQUIRED`` 停下.
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.application.context import dedup, downgrade, summarize, transcript
from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.application.llm.gateway.token_estimator import ApproximateTokenEstimator
from forgecli.application.llm.metering import UsageMeter
from forgecli.application.tools.artifact_store import ArtifactStore
from forgecli.domain.context.budget import ContextBudget
from forgecli.domain.context.compaction import CompactionDraft, CompactionLevel
from forgecli.domain.conversation.message import ChatMessage
from forgecli.domain.model.usage import UsageRecordDraft
from forgecli.domain.tool.result import ResultProvenance
from forgecli.domain.tool.tool_call import ToolSchema
from forgecli.shared.observability.log import get_log

__all__ = ["ContextFitResult", "ContextManager"]


_log = get_log(__name__)


@dataclass(frozen=True)
class ContextFitResult:
    """一次整理的产出."""

    messages: tuple[ChatMessage, ...]
    drafts: tuple[CompactionDraft, ...] = ()
    # 二级摘要那次模型调用的计量草稿 (ADR-0037). 与 drafts 分开: 一个说"省下多少
    # 上下文", 一个说"这次压缩花了多少 token", 是方向相反的两个数.
    #
    # 落盘由调用方负责, 与 CompactionDraft 和 UsageRecordDraft 完全同一条分工.
    usage_drafts: tuple[UsageRecordDraft, ...] = ()
    estimated_input: int = 0
    # 压完仍然放不下. 循环据此走 CONTEXT_COMPACTION_REQUIRED, 而不是把一个必然被
    # 网关拒掉的请求发出去.
    over_allowance: bool = False


class ContextManager:
    """无状态. 每次从传进来的 transcript 重新算.

    不存去重表是刻意的: 存了就要回答"人工 Shell 之后怎么失效"(ADR-0032 决策 5),
    而无状态根本没有旧事实可复用 —— 每条工具结果记的都是它自己执行那一刻的状态.
    """

    def __init__(
        self,
        *,
        artifacts: ArtifactStore | None = None,
        gateway: LlmGateway | None = None,
        estimator: ApproximateTokenEstimator | None = None,
        meter: UsageMeter | None = None,
    ) -> None:
        # artifacts 缺省为 None: 没接存储时降级不可用 (换成引用等于把内容删了还不
        # 告诉人去哪找), 但去重与变更通知照常生效 —— 它们不依赖归档.
        self._artifacts = artifacts
        # gateway 缺省为 None: 没接时只做到一级, 压不下去就如实报 over_allowance.
        self._gateway = gateway
        self._estimator = estimator or ApproximateTokenEstimator()
        # meter 缺省为 None: 没接计量时二级摘要照常跑, 只是这次调用不产出计量草稿.
        # 注入在这里而不是让调用方自己算 —— 自动压缩走 AgentLoop, /compact 走
        # AgentTurnService, 两条路都要计量, 而只有 ContextManager 同时在这两条路上.
        self._meter = meter

    def fit(
        self,
        messages: tuple[ChatMessage, ...],
        *,
        budget: ContextBudget | None,
        # 必填而不是给个空默认: ModelRequest 拒绝空 session_id, 所以漏传的后果是二级
        # 摘要在真正需要它的时候抛异常 —— 而那一刻上下文已经超长, 这一轮就彻底没救了.
        # 让类型检查在装配期就拦住, 比运行期发现强.
        session_id: str,
        turn_id: str,
        system_prompt: str = "",
        tools: tuple[ToolSchema, ...] = (),
    ) -> ContextFitResult:
        current, deduped = self._dedup(messages)
        if budget is None:
            # 没给预算就只做去重与通知. 不猜一个窗口大小 —— 猜小了平白压掉内容,
            # 猜大了等于没有这道防线.
            return ContextFitResult(messages=current)

        estimated = self._estimate(current, system_prompt, tools)
        if not budget.needs_compaction(estimated):
            _log.debug(
                "context.within_budget",
                estimated_input=estimated,
                trigger=budget.trigger,
                allowance=budget.allowance,
            )
            return ContextFitResult(messages=current, estimated_input=estimated)

        _log.info(
            "context.compaction_needed",
            estimated_input=estimated,
            trigger=budget.trigger,
            allowance=budget.allowance,
            messages=len(current),
        )
        drafts: list[CompactionDraft] = []
        usage: list[UsageRecordDraft] = []
        current, estimated = self._downgrade(
            current, estimated, system_prompt, tools, drafts, deduped
        )
        if budget.needs_compaction(estimated):
            current, estimated = self._summarize(
                current,
                estimated,
                system_prompt,
                tools,
                drafts,
                usage,
                session_id=session_id,
                turn_id=turn_id,
            )
        return ContextFitResult(
            messages=current,
            drafts=tuple(drafts),
            usage_drafts=tuple(usage),
            estimated_input=estimated,
            over_allowance=budget.over_allowance(estimated),
        )

    def change_notice(
        self, messages: tuple[ChatMessage, ...], provenance: ResultProvenance | None
    ) -> str:
        """一次写入之后要追加的变更通知; 没有历史读取就返回空串.

        与 `fit` 分开是刻意的: `fit` 每次调模型之前都跑, 是幂等的; 这一条只在工具结果
        回填的那一刻跑一次, 结果拼进那条结果的正文. 混进 `fit` 就要回答"怎么不重复
        追加", 而那个问题只有靠认自己写过的文本才答得上来.
        """
        return dedup.changed_notice(transcript.slots_of(messages), provenance)

    def summarize_now(
        self,
        messages: tuple[ChatMessage, ...],
        *,
        session_id: str,
        turn_id: str,
    ) -> ContextFitResult:
        """手动压缩 (/compact): 不问预算, 直接走二级.

        与 fit 分开而不是加一个 force 参数: 两者的触发条件与失败含义都不同. fit 是
        "快满了, 想办法腾地方", 压不动要报 over_allowance; 这里是用户明确要求, 压不动
        就是压不动, 没有"还是发不出去"这个后果.
        """
        before = self._estimate(messages, "", ())
        drafts: list[CompactionDraft] = []
        usage: list[UsageRecordDraft] = []
        compacted, after = self._summarize(
            messages,
            before,
            "",
            (),
            drafts,
            usage,
            session_id=session_id,
            turn_id=turn_id,
        )
        return ContextFitResult(
            messages=compacted,
            drafts=tuple(drafts),
            usage_drafts=tuple(usage),
            estimated_input=after,
        )

    # ---- 各级 ----

    def _dedup(
        self, messages: tuple[ChatMessage, ...]
    ) -> tuple[tuple[ChatMessage, ...], dict[tuple[int, int], str]]:
        """改写后的 transcript, 外加这一遍动过哪些位置.

        位置一并交出去是给降级用的: 去重给出的引用比降级引用信息更多 (它还说了"与第
        几次相同"), 让降级覆盖掉是净损失. 改写不动消息与块的下标, 两遍的键因此对得上.
        """
        slots = transcript.slots_of(messages)
        rewrites = dedup.plan_rewrites(slots, self._artifacts)
        return transcript.rewrite(messages, rewrites), rewrites

    def _downgrade(
        self,
        messages: tuple[ChatMessage, ...],
        estimated: int,
        system_prompt: str,
        tools: tuple[ToolSchema, ...],
        drafts: list[CompactionDraft],
        deduped: dict[tuple[int, int], str],
    ) -> tuple[tuple[ChatMessage, ...], int]:
        slots = transcript.slots_of(messages)
        rewrites = downgrade.plan_rewrites(slots, self._artifacts, already=deduped)
        if not rewrites:
            return messages, estimated
        compacted = transcript.rewrite(messages, rewrites)
        after = self._estimate(compacted, system_prompt, tools)
        _log.info(
            "context.downgraded",
            blocks_rewritten=len(rewrites),
            tokens_before=estimated,
            tokens_after=after,
        )
        drafts.append(
            CompactionDraft(
                level=CompactionLevel.DOWNGRADE,
                tokens_before=estimated,
                tokens_after=after,
                blocks_rewritten=len(rewrites),
            )
        )
        return compacted, after

    def _summarize(
        self,
        messages: tuple[ChatMessage, ...],
        estimated: int,
        system_prompt: str,
        tools: tuple[ToolSchema, ...],
        drafts: list[CompactionDraft],
        usage: list[UsageRecordDraft],
        *,
        session_id: str,
        turn_id: str,
    ) -> tuple[tuple[ChatMessage, ...], int]:
        if self._gateway is None:
            _log.warning("context.summary_skipped", reason="no_gateway")
            return messages, estimated
        compacted, summary = summarize.summarize(
            messages,
            self._gateway,
            session_id=session_id,
            turn_id=turn_id,
            meter=self._meter,
        )
        # 先记账再看结果: 请求已经发出去了, 摘要好不好用与这笔钱花没花无关.
        if summary.usage is not None:
            usage.append(summary.usage)
        if not summary.text:
            _log.warning("context.summary_skipped", reason="empty_summary")
            return messages, estimated
        after = self._estimate(compacted, system_prompt, tools)
        _log.info(
            "context.summarized",
            messages_replaced=summary.messages_replaced,
            tokens_before=estimated,
            tokens_after=after,
            provider=summary.provider,
            model=summary.model,
        )
        _log.debug("context.summary_text", text=summary.text)
        drafts.append(
            CompactionDraft(
                level=CompactionLevel.SUMMARY,
                tokens_before=estimated,
                tokens_after=after,
                messages_replaced=summary.messages_replaced,
                summary=summary.text,
                provider=summary.provider,
                model=summary.model,
            )
        )
        return compacted, after

    def _estimate(
        self,
        messages: tuple[ChatMessage, ...],
        system_prompt: str,
        tools: tuple[ToolSchema, ...],
    ) -> int:
        return self._estimator.estimate_input(
            messages=messages,
            system_prompt=system_prompt or None,
            tools=tools,
        )
