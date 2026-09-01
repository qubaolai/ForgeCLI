"""把一次"疑似被围栏拦下"的失败, 翻译成模型看得懂的下一步.

围栏在子进程那一侧没有干净的信号 (见 `domain/execution/denial`), 命令自己报出来的就是
一句 `Permission denied`. 模型读不出这是"这个文件本来就只读"还是"围栏不让写", 而这两者
该走完全不同的下一步 —— 前者换个路径, 后者要请用户放开边界.

日志里这个区别的代价是可量化的: 一次 `mvn` 被拦之后, 模型连发五条命令满机器找 maven,
五次模型调用合计二十万输入 token, 而正确的下一步是一句话告诉用户.

## 方向

这里**只产出一段说明**, 不放行任何东西. 放开边界仍然要用户自己去授权目录
(`WorkspaceGrants`, 那一层明写"只能由用户发起, LLM 不能自己调用来扩大访问范围").

所以认错了的代价是"多了一句没用的提示", 认漏了的代价是"回到今天的样子". 两个方向都不
通往越界, 这是这条启发式可以接受的前提.
"""

from __future__ import annotations

from forgecli.application.prompt.template_renderer import render_notice
from forgecli.domain.execution.denial import blocked_paths
from forgecli.domain.execution.fence import FencePolicy
from forgecli.domain.workspace.boundary import is_within

__all__ = ["fence_hint"]


def fence_hint(output: str, fence: FencePolicy | None, *, confined: bool) -> str:
    """疑似被围栏拦下时的说明; 没有疑似就返回空串.

    `confined` 为假时一律不出提示: 围栏根本没立起来, 那句 `Permission denied` 只能是
    文件系统本身的权限, 说成围栏就是在骗模型.
    """
    if not confined or fence is None:
        return ""
    candidates = [
        path
        for path in blocked_paths(output)
        if not _already_writable(path, fence) and not _protected(path, fence)
    ]
    if not candidates:
        return ""
    return render_notice("loop.fence_blocked", paths=tuple(candidates))


def _already_writable(path: str, fence: FencePolicy) -> bool:
    """围栏本来就允许写这里 —— 那这次拒绝与围栏无关, 是真的权限不够."""
    return any(is_within(path, root) for root in fence.all_writable)


def _protected(path: str, fence: FencePolicy) -> bool:
    """受保护路径授权不开, 提议它只会让用户点一个必然失败的按钮."""
    return any(is_within(path, root) for root in fence.denied_read_paths)
