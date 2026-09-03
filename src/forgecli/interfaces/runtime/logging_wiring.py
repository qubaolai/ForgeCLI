"""进程入口的日志与指标装配 (ADR-0035).

住在 interfaces 而不是 shared: 决定"日志写到哪一级, 写到哪个目录"要读应用配置与
Forge home, 而那是 infrastructure 的知识. `shared/observability` 只接受解析好的值,
于是它对任何层都是安全的依赖.

唯一的启动路径 (裸 `forge` 起 Web, ADR-0025) 调一次 `start_observability()`,
位置在最前面 —— 早于信任解析与进程锁, 这样"起不来"本身也留得下记录.

## 为什么直接读 config.json 而不经 ConfigService

装配发生在任何 service 之前. 装 service 要先解析项目, 解析项目要先能记日志 —— 反过来
就成环了. 所以这里读的是同一个文件的原始键值, 校验交给后面的配置服务 (它会把写坏的值
报出来). 读不动就按默认值装, 一个坏掉的 config.json 不该让 forge 起不来, 更不该让它在
"连日志都还没有"的状态下起不来.
"""

from __future__ import annotations

from pathlib import Path

from forgecli.domain.config import config_keys
from forgecli.domain.config.effective_config import EffectiveConfig
from forgecli.infrastructure.config import JsonConfigStore, config_dir, config_file
from forgecli.shared.errors import ConfigError
from forgecli.shared.observability.configure import LoggingStatus, configure_logging

__all__ = ["start_observability"]


def start_observability() -> LoggingStatus:
    """按 `logging.*` 装上日志 handler."""
    config = _startup_config()
    directory = (
        Path(config.logging_directory).expanduser()
        if config.logging_directory.strip()
        else config_dir() / "logs"
    )
    return configure_logging(
        directory=directory,
        level=config.logging_level,
        console=config.logging_console,
        max_value_chars=config.logging_max_value_chars,
        include_http=config.logging_include_http,
    )


def _startup_config() -> EffectiveConfig:
    """只取应用级的那几个键; 项目级配置这时候还没解析出来."""
    try:
        raw = JsonConfigStore(config_file("config.json")).load()
    except ConfigError:
        raw = {}
    app_keys = {key.name for key in config_keys.keys_for(config_keys.ConfigLevel.APP)}
    return EffectiveConfig.from_overrides(
        {name: value for name, value in raw.items() if name in app_keys}
    )
