"""启动期特权探测: 当前进程是否以 root / 管理员身份运行.

ADR-0009 决策 2: 整套安全模型的外墙是 OS 用户权限 —— Agent 以调用者的普通用户身份
运行, 系统级灾难由 OS 挡住. 以 root 运行等于拆掉这堵墙, 此时 Forge 无法再声称安全,
所以 bootstrap 在进入 REPL 前用本模块**拒绝启动**(而非降级).

决策 13 明令"配置只能收紧, 不能放宽内置边界", 其中就包括"不允许关闭 root 校验" ——
故本模块不读取任何配置项, 也不读环境变量, 没有旁路开关.

放在 interfaces/cli 而不是 infrastructure: 这是一次无状态的 OS 只读探测, 没有对应的
application port, 唯一消费者是 CLI 启动流程 —— 与同层的 stdin_is_tty() 同性质.
infrastructure 那边放的是有 port 背书的 store / adapter.

平台分派沿用 process_lock 的写法: 模块底部按 sys.platform 定义同签名实现, 调用方只
import is_elevated, 不关心平台. 不用 tty/ 那种"分文件再 import"的写法, 因为 mypy 会
按当前解释器整段剪掉 win32 分支, 内联可以免掉逐处 type: ignore.
"""

from __future__ import annotations

import os
import sys

__all__ = ["is_elevated"]


if sys.platform == "win32":
    import ctypes

    def is_elevated() -> bool:
        """Windows: 进程是否处于管理员提权状态.

        探测失败按"未提权"处理: 探测本身出问题不该把普通用户挡在门外, 而对未提权
        用户来说 OS 权限外墙依然生效.
        """
        try:
            shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
            return bool(shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False

else:

    def is_elevated() -> bool:
        """POSIX: euid == 0 即 root, 同时覆盖 sudo forge 与 root shell 两条路径."""
        return os.geteuid() == 0
