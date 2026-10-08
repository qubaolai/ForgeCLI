"""进程入口的日志与指标装配 (ADR-0035).

住在 interfaces 而不是 shared: 决定"日志写到哪一级, 写到哪个目录"要读应用配置与
Forge home, 而那是 infrastructure 的知识. `shared/observability` 只接受解析好的值,
于是它对任何层都是安全的依赖.

两条启动路径 (裸 `forge` 起 Web, `forge --cli` 进终端) 各调一次 `start_observability()`,
位置在最前面 —— 早于信任解析与进程锁, 这样"起不来"本身也留得下记录.

应用级配置在这时候怎么读, 见 `interfaces/runtime/startup_config`: 装配发生在任何
service 之前, 所以那条路径直接读 config.json 的原始键值.
"""

from __future__ import annotations

from pathlib import Path

from forgecli.common.observability.configure import LoggingStatus, configure_logging
from forgecli.common.runtime_files import runtime_dir
from forgecli.settings.startup import startup_config

__all__ = ["start_observability"]


def start_observability() -> LoggingStatus:
    """按 `logging.*` 装上日志 handler."""
    config = startup_config()
    directory = (
        Path(config.logging_directory).expanduser()
        if config.logging_directory.strip()
        else runtime_dir() / "logs"
    )
    return configure_logging(
        directory=directory,
        level=config.logging_level,
        console=config.logging_console,
        max_value_chars=config.logging_max_value_chars,
        include_http=config.logging_include_http,
    )
