"""ManualMutationBarrier: 从人工 Shell 回来之后的失效屏障 (ADR-0017 §10).

人工 Shell 可以在 Forge 完全观察不到的情况下改工作区, 依赖, 可执行文件与配置 —— 而
Forge **有意**不捕获它的内容 (§9), 所以没有任何办法推断改了什么. 唯一正确的假设是
"可能发生了任意外部变更", 于是回来之后把所有基于旧事实的东西一次性作废.

两个机制:

1. **失效回调.** 每个持有缓存的组件在装配期注册一个清理函数. 注册制而不是让屏障去
   认识每一个缓存: 后者意味着新增一处缓存要记得改屏障, 而"忘了改"是静默的.
2. **generation 计数.** 单调递增, 任何想跨人工 Shell 复用事实的地方都可以拿它当键.
   回调只能清掉**已知**的缓存, 计数器能让**将来**新增的缓存也有一个正确的失效判据.

失效失败必须**阻止下一次 Agent turn** (§12): 清不掉缓存就无法证明后续裁决基于当前事实,
而"基于过期事实的 Allow"正是这套机制要防的东西. 宁可让用户重启, 不能继续接收指令.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

__all__ = ["BarrierOutcome", "ManualMutationBarrier"]


@dataclass(frozen=True)
class BarrierOutcome:
    generation: int
    invalidated: tuple[str, ...] = ()
    failures: tuple[tuple[str, str], ...] = field(default=())

    @property
    def clean(self) -> bool:
        return not self.failures

    @property
    def report(self) -> str:
        """给用户看的诊断. 失败时必须说清是哪一项, 否则无从下手."""
        if self.clean:
            return f"工作区事实已失效 ({len(self.invalidated)} 项), 下一轮重新读取."
        listed = "; ".join(f"{name}: {why}" for name, why in self.failures)
        return f"失效屏障未能完成, 已阻止后续 Agent turn. 失败项: {listed}"


class ManualMutationBarrier:
    def __init__(self) -> None:
        self._hooks: dict[str, Callable[[], None]] = {}
        self._generation = 0
        self._blocked_by: BarrierOutcome | None = None

    def register(self, name: str, invalidate: Callable[[], None]) -> None:
        """注册一处需要失效的缓存. 同名重复注册直接报错 —— 那多半是装配写重了,
        而后一个静默覆盖前一个会让其中一处缓存永远清不掉."""
        if name in self._hooks:
            raise ValueError(f"失效钩子重复注册: {name}")
        self._hooks[name] = invalidate

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def blocked(self) -> bool:
        return self._blocked_by is not None

    @property
    def block_reason(self) -> str:
        return "" if self._blocked_by is None else self._blocked_by.report

    def trip(self) -> BarrierOutcome:
        """执行失效. 无论人工 Shell 成功, 失败还是崩溃都要调 —— 起不来的 Shell 也可能
        已经跑过 rc 文件, 而"启动失败"不等于"什么都没发生"."""
        # generation 先加: 事实**确实**已经过期, 这一点与回调跑没跑成功无关.
        self._generation += 1
        done: list[str] = []
        failures: list[tuple[str, str]] = []
        for name, hook in self._hooks.items():
            try:
                hook()
            except Exception as exc:  # 一个钩子炸掉不能让其余的不执行
                failures.append((name, f"{type(exc).__name__}: {exc}"))
            else:
                done.append(name)
        outcome = BarrierOutcome(
            generation=self._generation,
            invalidated=tuple(done),
            failures=tuple(failures),
        )
        self._blocked_by = None if outcome.clean else outcome
        return outcome
