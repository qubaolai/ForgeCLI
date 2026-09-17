"""供应商可用性判定。

一家供应商是否可用 = 网关真正会用的那几把 key (ProviderRuntimeSettings.credential_refs,
即 `credential_refs` 或由 `api_key_env` 派生的单条) 里, 至少有一个环境变量当前被设置
（非空）。凭证本身不进配置文件、不被读取内容——这里只看「在不在」。

走 LlmConfigProviderSettingsSource 而不是注册表 (ADR-0059 决策 7): 用户在 /config 里
给一家供应商配了 `credential_refs = [A, B]` 之后, 网关用的是 A / B, 而注册表只知道
默认的那个环境变量名 —— 按注册表判会让一家能正常调用的供应商在界面上显示"缺少 XXX".

职责边界（与 EnvCredentialResolver 划清，不另立第二套凭证概念）：
    - 本类只做「provider 声明的 key 名是否设置了」的轻量判断，服务于 /model 面板置灰。
    - 凭证「值」的解析 / auth 错误归一化，统一由 EnvCredentialResolver 负责
      （凭证引用即环境变量名）；provider adapter 亦不得自行读环境变量。
"""

from __future__ import annotations

import os

from forgecli.common.errors import ConfigReadError
from forgecli.llm.errors import UnknownProvider
from forgecli.llm.gateway.provider_settings import LlmConfigProviderSettingsSource


class EnvProviderAvailability:
    """基于环境变量: 凭证池里的环境变量名是否有一个被设置为非空值."""

    def __init__(self, settings: LlmConfigProviderSettingsSource) -> None:
        self._settings = settings

    def credential_refs(self, provider_id: str) -> tuple[str, ...]:
        """网关会用的环境变量名, 按池的顺序; 未知供应商或免密钥的端点为空.

        每次现读配置: /config 里刚改完的池要立刻反映在"已就绪 / 缺少"上.
        """
        try:
            return self._settings.settings_for(provider_id).credential_refs
        except (UnknownProvider, ConfigReadError):
            return ()

    def is_available(self, provider_id: str) -> bool:
        """只看环境变量在不在, 不读它的值.

        免密钥的端点 (本地推理, 或者自建的无鉴权服务) 返回 False: 界面上说"无需密钥",
        那是另一句话, 不是"就绪".
        """
        return any(
            os.environ.get(ref, "").strip() for ref in self.credential_refs(provider_id)
        )
