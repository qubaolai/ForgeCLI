"""默认配置。

默认值由 keys.SCHEMA 推导，保证“未配置时返回默认”这一行为有唯一来源，
不在多处硬编码默认值。
"""

from __future__ import annotations

from forgecli.domain.config import config_keys
from forgecli.domain.config.effective_config import EffectiveConfig

# 键 -> 默认规范字符串
DEFAULTS: dict[str, str] = {key.name: key.default for key in config_keys.SCHEMA}


def default_config() -> EffectiveConfig:
    """无任何用户覆盖时的有效配置。"""
    return EffectiveConfig.from_overrides({})
