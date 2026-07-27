"""配置应用层：读写用例与存储抽象。

键 schema / 生效配置视图 / 键相关错误都住在 domain.config, 本包不转手再导出；
TOML 文件读写属于 infrastructure，本包不感知文件格式与磁盘细节。
"""

from __future__ import annotations

from forgecli.application.config.config_service import ConfigService
from forgecli.application.config.config_store import ConfigStore

__all__ = [
    "ConfigService",
    "ConfigStore",
]
