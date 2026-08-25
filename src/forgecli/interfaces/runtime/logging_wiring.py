"""进程入口的日志装配 (ADR-0035).

住在 interfaces 而不是 shared: 决定"日志写到哪一级, 写到哪个目录"要读应用配置与
Forge home, 而那是 infrastructure 的知识. `shared/observability` 只接受一个目录和一个
级别字符串, 于是它对任何层都是安全的依赖.

两条启动路径 (`forge` 起 Web, `forge cli` 进终端) 各调一次 `start_logging()`, 位置都在
最前面 —— 早于信任解析与进程锁, 这样"起不来"本身也留得下记录.
"""

from __future__ import annotations

from forgecli.domain.config.config_keys import LOGGING_LEVEL
from forgecli.infrastructure.config import JsonConfigStore, config_dir, config_file
from forgecli.shared.errors import ConfigError
from forgecli.shared.observability.configure import LoggingStatus, configure_logging

__all__ = ["logs_dir", "start_logging"]


def logs_dir() -> str:
    """日志目录: Forge home 下的 logs/. 与会话事件, 恢复快照同一层信任假设."""
    return str(config_dir() / "logs")


def start_logging() -> LoggingStatus:
    """读应用配置里的 logging.level 并装上 handler.

    配置文件读不动时不抛: 一个坏掉的 config.json 不该让 forge 起不来, 更不该让它在
    "连日志都还没有"的状态下起不来. 那种情况按默认级别装配, 后面配置服务自己会报错.
    """
    return configure_logging(directory=config_dir() / "logs", level=_configured_level())


def _configured_level() -> str:
    try:
        return JsonConfigStore(config_file("config.json")).load().get(LOGGING_LEVEL, "")
    except ConfigError:
        return ""
