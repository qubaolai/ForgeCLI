"""Turn 级取消信号源：控制面的"停止"与循环取消 token 的接线点。

ProjectRuntime 持有单例：每轮开始 issue() 新 token，结束时 clear()；loop 工厂经
current 读取当前 token 挂到 ModelRequest 上。application 层只依赖 CancelToken，
不感知 HTTP 与信号细节。
"""

from __future__ import annotations

from forgecli.shared.cancellation import CancelToken


class TurnCancelSource:
    """当前 turn 的取消 token 持有者（同一时刻至多一轮在跑）。"""

    def __init__(self) -> None:
        self._current: CancelToken | None = None

    def issue(self) -> CancelToken:
        """开启一轮: 创建并持有新 token"""
        token = CancelToken()
        self._current = token
        return token

    def current(self) -> CancelToken | None:
        """当前在途 turn 的 token；无在途 turn 时为 None。"""
        return self._current

    def clear(self) -> None:
        """一轮结束：释放持有的 token。"""
        self._current = None
