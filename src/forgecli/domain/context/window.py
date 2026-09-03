"""会话窗口: 只追加, 罕见地整体淘汰 (ADR-0041 决策 4 / 决策 5).

## 只追加

窗口一旦写进去就不再改. 去重, 降级与变更通知这三条原先会回头改写它的通路已经删除 ——
在前缀缓存下, 改一处的成本等于它自己加上它后面的全部内容. dedup.py 实测过: 一次
`fs_apply_patch` 之后改写中段, 17,154 输入里 7,298 未命中; 改文件密集的那一轮三次改写
合计 79,321, 占 57%. 省下的不过是把一份 4k 正文换成 40 token 占位, 净亏五到十倍.

`transcript.rewrite` 因此一起删了: 留着机制入口, 就等于留着退回去的路, 而"回头整理一下
历史"永远是看起来合理的.

## 批量淘汰, 不逐条滑动

"保留最近 N 条"每轮从头部丢一条, 而头部就是前缀的开头 —— 每一轮都在付整段重算的钱.
分段批量淘汰把作废频率从每轮一次降到每若干十步一次, 平均窗口长度不变.

推广成通则: **不得不作废前缀时, 要罕见且大块, 绝不能连续小步.**
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.conversation.message import ChatMessage
from forgecli.domain.conversation.turn import MessageRole

__all__ = ["EvictionPlan", "Window", "WindowPolicy"]


@dataclass(frozen=True)
class WindowPolicy:
    """两条水位与最小保留步数.

    水位由 ``ContextBudget`` 扣掉前缀之后导出, 不手写: 手写的数在换一个模型之后就不对了,
    而它不会报错, 只会让淘汰要么太早要么根本不发生.
    """

    high_water_tokens: int
    low_water_tokens: int
    # 淘汰之后至少留几条消息. 留的是最近的 —— 模型正在做的那件事全在这几条里.
    min_messages: int = 6

    def __post_init__(self) -> None:
        if self.high_water_tokens <= 0:
            raise ValueError("WindowPolicy.high_water_tokens 必须为正整数")
        if not 0 < self.low_water_tokens < self.high_water_tokens:
            raise ValueError("WindowPolicy.low_water_tokens 必须落在 (0, 高水位)")
        if self.min_messages < 1:
            raise ValueError("WindowPolicy.min_messages 必须为正整数")

    def assert_fits(self, *, max_inline_bytes: int, chars_per_token: int = 3) -> None:
        """水位判据: **一条满额的工具结果装得下** (ADR-0041 决策 8).

        由 `WindowManager` 在导出水位之后调一次. 它不能是纯构建期检查 —— 水位跟着当前
        模型走, 而用户随时可以换模型; 能在装配时定死的只有 `max_inline_bytes` 那一半.

        判据只取一条而不是 ``min_messages`` 条: 只有 `fs_read` 一个工具把正文带进窗口,
        而 ``max_inline_bytes`` 是上限不是常量. 按"每一条都满额"去算, 会把一个完全正常的
        64k 模型也拒掉 —— 一条不该响的警报比没有警报更糟.

        取一条的理由是它划出了真正无解的那条线: 淘汰之后窗口里必然还留着最近那一条,
        如果连它自己都超过低水位, 那么淘汰多少次都没有用. 那是**配置**错了.

        小窗口模型正是这条会咬住的地方: 要么关掉 body_in_window, 要么收
        max_inline_bytes, 要么换一个窗口更大的模型.
        """
        needed = max_inline_bytes // chars_per_token
        if needed >= self.low_water_tokens:
            raise ValueError(
                f"一条满额工具结果约需 {needed} token, "
                f"而低水位只有 {self.low_water_tokens}: "
                "调小 max_inline_bytes, 关掉 body_in_window, 或者换一个窗口更大的模型"
            )


@dataclass(frozen=True)
class EvictionPlan:
    """一次淘汰要丢掉哪一段.

    ``split_index`` 之前的消息被丢弃, 之后的留下. 切点由 ``safe_split_points`` 给出,
    必然落在 tool call 配对之外.
    """

    split_index: int
    dropped: tuple[ChatMessage, ...]
    kept: tuple[ChatMessage, ...]
    # 被丢掉的那一段里的用户原话, 逐字保留 (ADR-0041 决策 5).
    preserved_user_messages: tuple[ChatMessage, ...]

    @property
    def empty(self) -> bool:
        return not self.dropped


@dataclass(frozen=True)
class Window:
    """会话窗口. 只追加, 每次操作返回新实例."""

    messages: tuple[ChatMessage, ...] = ()
    # 累计淘汰过几条. 只进日志与事件, 不参与任何判断.
    evicted_count: int = 0

    def append(self, *messages: ChatMessage) -> Window:
        return Window(
            messages=(*self.messages, *messages), evicted_count=self.evicted_count
        )

    def replace_messages(self, messages: tuple[ChatMessage, ...]) -> Window:
        """整体换掉消息序列. 只给淘汰与会话重建用.

        它不是"改写"的后门: 淘汰本来就要重排头部, 而重建是从事件重放出一份新的窗口.
        两者都不是在**已发出的前缀中段**动手, 那才是被禁的那件事.
        """
        return Window(messages=messages, evicted_count=self.evicted_count)

    def plan_eviction(
        self, *, split_points: tuple[int, ...], keep_last: int, verbatim_chars: int
    ) -> EvictionPlan:
        """挑一个切点, 丢掉它之前的全部.

        在合法切点里挑**最靠后**的那一个, 只要它还能留下 ``keep_last`` 条. 靠后意味着
        这次多丢一些, 下一次淘汰离得更远 —— 而淘汰次数才是代价, 每一次都作废一整个前缀.

        ``verbatim_chars`` 是逐字保留的用户原话的字符上限, 见 `_verbatim`.
        """
        limit = max(0, len(self.messages) - keep_last)
        chosen = 0
        for point in split_points:
            if point <= limit:
                chosen = point
        dropped = self.messages[:chosen]
        return EvictionPlan(
            split_index=chosen,
            dropped=dropped,
            kept=self.messages[chosen:],
            preserved_user_messages=self._verbatim(dropped, verbatim_chars),
        )

    def _verbatim(
        self, dropped: tuple[ChatMessage, ...], budget_chars: int
    ) -> tuple[ChatMessage, ...]:
        """被丢掉那一段里值得逐字保留的用户原话.

        两条限制, 缺一条这一支就会把淘汰本身抵消掉:

        **① 跳过上一份交接说明.** 淘汰过之后, ``messages[0]`` 就是我们自己拼的那条交接
        说明, 而它走的是 user 角色. 不跳过的话, 它里面已经保留过一次的原话会被再逐字抄
        一遍, 下次再抄一遍 —— 每淘汰一次翻一倍, 而淘汰正是为了变小.

        **② 有字符上限, 且留最早的.** 没有上限的话, 一段全是用户消息的窗口淘汰完比淘汰前
        还大 (实测: 高水位 4000, 淘汰后 16212). 留最早的而不是最近的, 是因为目标与约束
        通常出现在开头, 而那正是最不能被转述走样的东西; 后面的内容有交接说明兜着.
        """
        start = 1 if self.evicted_count > 0 else 0
        kept: list[ChatMessage] = []
        used = 0
        for message in dropped[start:]:
            if message.role is not MessageRole.USER:
                continue
            size = sum(len(getattr(block, "text", "")) for block in message.content)
            if used + size > budget_chars:
                break
            kept.append(message)
            used += size
        return tuple(kept)
