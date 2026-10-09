"""`--allow-full-access` 的两道门 (ADR-0052 决策 1 / 2 / 3).

Bypass Permissions (旧名 Full Access) 档不在默认的模式列表里: 启动时带
`--allow-full-access` 才把它放进 `available_modes`, 而带了参数还要依次过两道门 ——

1. 应用级配置 `security.disable_full_access` 没锁 (决策 3). 锁着就拒绝启动, 而不是静默
   去掉参数: 用户带了参数却发现菜单里没有那一档, 会去翻文档而不是翻配置.
2. 这台机器上有人在交互式终端里确认过一次 (决策 2). 记录在
   `infrastructure/security/full_access_consent`, 以后不再问. 没有终端就没有人能回答,
   那时拒绝启动并说清楚去哪里确认.

住在 `interfaces/` 顶层而不是某个入口的包里: 两条启动路径 (`tui/bootstrap`,
`web/server`) 都要过它, 而它既要读配置 (infrastructure) 又要在终端里问人 (tui/chooser).
两边各写一份, 漂移的那一份不会报错.
"""

from __future__ import annotations

import sys

from rich.console import Console
from rich.text import Text

from forgecli.common.observability.log import get_log
from forgecli.security.bypass_consent import consent_recorded, record_consent
from forgecli.settings import keys as config_keys
from forgecli.settings.startup import startup_config
from forgecli.tui.chooser import confirm
from forgecli.tui.console import STYLE_DIM

__all__ = ["FullAccessRefused", "resolve_full_access"]

_log = get_log(__name__)

# 确认前给人看的那几行. 说的是这一档 (bypass_permissions, ADR-0055 决策 1) 的语义,
# 不是泛泛的"危险": deny 规则, 内容级 ask 规则与安全检查照拦, 变的只是其余的还问不问.
_WARNING = (
    "Bypass Permissions: 除显式规则与安全检查外不再询问."
)


class FullAccessRefused(Exception):
    """带了 `--allow-full-access` 却过不了门. message 可直接展示, 退出码固定为
    `ExitCode.FULL_ACCESS_REFUSED`."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def resolve_full_access(console: Console, *, allow_full_access: bool) -> bool:
    """本进程能否把 Full Access 放进可选预设.

    没带参数直接回 False, 一个字都不打 —— 绝大多数启动走的是这条. 带了参数就过两道门,
    哪一道没过都抛 FullAccessRefused.
    """
    if not allow_full_access:
        return False
    if startup_config().disable_full_access:
        _log.warning("forge.full_access_refused", reason="disabled_by_config")
        raise FullAccessRefused(
            f"配置 {config_keys.SECURITY_DISABLE_FULL_ACCESS} 锁死了 Full Access 档: "
            "去掉 --allow-full-access, 或先用 /config 关掉这个开关再启动."
        )
    if consent_recorded():
        _log.info("forge.full_access_allowed", consent="recorded")
        return True
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        _log.warning("forge.full_access_refused", reason="no_tty_for_consent")
        raise FullAccessRefused(
            "第一次放开 Full Access 要有人在交互式终端里确认一次: 在终端里跑 "
            "forge --cli --allow-full-access (或裸 forge --allow-full-access) 回答那个"
            "问题, 之后不再问."
        )
    for line in _WARNING:
        console.print(Text(line, style=STYLE_DIM))
    try:
        accepted = confirm(console, "放开 Full Access 档?")
    except (KeyboardInterrupt, EOFError):
        # 在这个问句上按 Ctrl-C 的意思是"不放开", 与 tui/bootstrap 的信任问句同一个理由.
        console.print()
        accepted = False
    if not accepted:
        _log.warning("forge.full_access_refused", reason="declined")
        raise FullAccessRefused(
            "没有确认, Forge 不以放开 Full Access 的方式启动. "
            "去掉 --allow-full-access 再试."
        )
    record_consent()
    _log.info("forge.full_access_allowed", consent="just_recorded")
    return True
