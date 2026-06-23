"""供应商可用性判定。

一家供应商是否可用 = 它在 providers.REGISTRY 里声明的 api_key_env 环境变量
当前是否被设置（非空）。凭证本身不进配置文件、不被读取内容——这里只看「在不在」。
"""

from __future__ import annotations

import os

from forgecli.application.llm import providers as provider_registry


class EnvProviderAvailability:
    """基于环境变量: spec.api_key_env 是否被设置为非空值"""

    def is_available(self, provider_id: str) -> bool:
        spec = provider_registry.REGISTRY.get(provider_id)
        if spec is None or not spec.api_key_env:
            return False
        return bool(os.environ.get(spec.api_key_env, "").strip())
