"""进程启动时读应用级配置: 日志装配与 Full Access 的锁都在任何 service 之前要用到.

## 为什么直接读 config.json 而不经 ConfigService

装配发生在任何 service 之前. 装 service 要先解析项目, 解析项目要先能记日志 —— 反过来
就成环了. 所以这里读的是同一个文件的原始键值, 校验交给后面的配置服务 (它会把写坏的值
报出来). 读不动就按默认值装, 一个坏掉的 config.json 不该让 forge 起不来, 更不该让它在
"连日志都还没有"的状态下起不来.

`security.disable_full_access` 走同一条路 (ADR-0052): 它按默认值 (不锁) 装的后果是
少拒绝一次 `--allow-full-access`, 而那一次仍然要过一次性确认那道门.
"""

from __future__ import annotations

from forgecli.common.errors import ConfigError
from forgecli.settings import keys as config_keys
from forgecli.settings.config_store import JsonConfigStore
from forgecli.settings.effective import EffectiveConfig
from forgecli.settings.paths import config_file

__all__ = ["startup_config"]


def startup_config() -> EffectiveConfig:
    """只取应用级的那几个键; 项目级配置这时候还没解析出来."""
    try:
        raw = JsonConfigStore(config_file("config.json")).load()
    except ConfigError:
        raw = {}
    app_keys = {key.name for key in config_keys.keys_for(config_keys.ConfigLevel.APP)}
    return EffectiveConfig.from_overrides(
        {name: value for name, value in raw.items() if name in app_keys}
    )
