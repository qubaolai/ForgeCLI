"""协作式取消信号.

CancelToken 原本住在 domain/model/request.py, 但它与"模型请求"没有任何关系, 只是恰好
第一个用到它的是 LLM gateway. ADR-0004 §10 要求取消信号贯穿 ToolRuntime.execute, 让
工具系统从 domain.model 里 import 一个取消 token 就说不通了, 故上移到 shared.

它是有意可变的: 表达的是"某个正在跑的活儿被要求停下", 按身份比较而不是按值.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["CancelToken"]


@dataclass(eq=False)
class CancelToken:
    """协作式取消: 被取消方自己检查 cancelled, 不做强制中断."""

    _cancelled: bool = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled
