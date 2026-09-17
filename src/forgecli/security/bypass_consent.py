"""Full Access 的一次性确认记录 (ADR-0052 决策 2).

第一次带 `--allow-full-access` 启动时, 人要在终端里对"这一档除 Hard Deny 外不再问你"
点一次头. 点过就记在这里, 以后不再问 —— 与 Claude Code 把 bypassPermissions 的确认存进
用户级设置同一个做法.

**它是状态, 不是配置偏好**: 记的是"某时某刻有人确认过", 所以不进 `config_keys.SCHEMA`,
也就不能经 `/config` 写出来 —— 跳过对话框的唯一办法是真的在终端里回答一次. 文件落在
Forge home 而不是项目目录: 确认的是这台机器上的这个人, 不是某个仓库.
"""

from __future__ import annotations

from forgecli.common.clock import now_iso
from forgecli.common.errors import ConfigError
from forgecli.common.json_io import read_document, write_document
from forgecli.settings.paths import config_file

__all__ = ["consent_recorded", "record_consent"]

_FILE = "full-access-consent.json"
_KEY = "accepted_at"


def consent_recorded() -> bool:
    """有人确认过吗. 文件读不动按"没确认"办: 那只会多问一次, 不会少问."""
    try:
        return bool(read_document(config_file(_FILE)).get(_KEY))
    except ConfigError:
        return False


def record_consent() -> None:
    write_document(config_file(_FILE), {_KEY: now_iso()})
