"""配置的领域词汇: 键 schema, 生效配置, 键相关错误。"""

from forgecli.domain.config.config_keys import (
    SCHEMA,
    ConfigKey,
    ConfigLevel,
    ValueKind,
    is_known,
    keys_for,
    require_known,
)
from forgecli.domain.config.effective_config import EffectiveConfig
from forgecli.domain.config.errors import UnknownConfigKey

__all__ = [
    "SCHEMA",
    "ConfigKey",
    "ConfigLevel",
    "EffectiveConfig",
    "UnknownConfigKey",
    "ValueKind",
    "is_known",
    "keys_for",
    "require_known",
]
