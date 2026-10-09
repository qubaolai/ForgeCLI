"""Full Access 的一次性确认记录.

第一次带 `--allow-full-access` 启动时, 人要在终端里对"开启 allow_full_access"
进行一次确认. 后续再以 allow_full_access 启动 不会再询问.
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
    """检查用户是否确认过开启 allow_full_access."""
    try:
        return bool(read_document(config_file(_FILE)).get(_KEY))
    except ConfigError:
        return False


def record_consent() -> None:
    """用户确认过开启 allow_full_access 写入记录, 记录确认时间."""
    write_document(config_file(_FILE), {_KEY: now_iso()})
