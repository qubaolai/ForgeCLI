"""供应商可用性判定。

一家供应商是否可用 = 它在 providers.REGISTRY 里声明的 api_key_env 环境变量
当前是否被设置（非空）。凭证本身不进配置文件、不被读取内容——这里只看「在不在」。

职责边界（与 07-03 CredentialResolver 划清，不另立第二套凭证概念）：
    - 本类只做「provider 是否声明并设置了 key 名」的轻量判断，服务于 /model 面板置灰。
    - 凭证「值」的解析 / auth 错误归一化，统一由 CredentialResolver 负责
      （凭证引用即环境变量名）；provider adapter 亦不得自行读环境变量。

已知细化点（07-03 处理）：local 等免凭证 provider 目前因 api_key_env 为空被判为不可用；
待 CredentialResolver 落地后再把「免凭证 provider」视为可用。
"""

from __future__ import annotations

import os

from forgecli.application.llm import providers as provider_registry
from forgecli.application.llm.errors import UnknownProvider


class EnvProviderAvailability:
    """基于环境变量: spec.api_key_env 是否被设置为非空值"""

    def is_available(self, provider_id: str) -> bool:
        """只看环境变量在不在, 不读它的值.

        走合并后的查找而不是直接翻 REGISTRY: 用户自建的供应商不在内置表里, 而按 REGISTRY
        查会让它们一律得到 False —— 界面上那句"缺少 XXX"于是对每一家自建供应商都成立,
        哪怕变量其实配好了.
        """
        try:
            spec = provider_registry.require_known_provider(provider_id)
        except UnknownProvider:
            return False
        if not spec.api_key_env:
            # 免密钥的端点 (本地推理, 或者自建的无鉴权服务). 界面上说"无需密钥",
            # 那是另一句话, 不是"就绪".
            return False
        return bool(os.environ.get(spec.api_key_env, "").strip())
