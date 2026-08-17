"""ManualShellResult: 一次人工 Shell 会话是怎么结束的 (ADR-0017 §5).

**不含命令, 输入, 输出, 环境变量值或子进程参数** (§9). 这不是"暂时没做", 而是这个类
故意只能装下这些字段 —— 想加一个 ``captured_output`` 进来, 得先改 ADR.

三种结束方式在类型上分开, 因为界面对它们的说法不一样:

- 正常结束 -> ``exit_code`` 有值.
- 被信号杀掉 -> ``signal`` 有值. 不能显示成"命令失败", 它没跑完.
- 压根没起来 -> ``start_error`` 有值. 这是 Forge 的问题, 不是用户命令的问题 (§12).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ManualShellResult"]


@dataclass(frozen=True)
class ManualShellResult:
    started_at: str
    finished_at: str
    exit_code: int | None = None
    signal: int | None = None
    start_error: str | None = None

    def __post_init__(self) -> None:
        if self.start_error and (self.exit_code is not None or self.signal is not None):
            raise ValueError("没启动成功就不会有退出码或信号")

    @property
    def started(self) -> bool:
        return self.start_error is None

    @property
    def summary(self) -> str:
        """返回 Forge 时打的那一行. 不带任何 Shell 内容."""
        if self.start_error:
            return f"未能进入 Shell: {self.start_error}"
        if self.signal is not None:
            return f"返回 Forge · shell 被信号 {self.signal} 终止"
        return f"返回 Forge · shell exit {self.exit_code}"
