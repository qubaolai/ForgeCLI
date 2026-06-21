"""供应商封闭注册表（代码侧，唯一权威）。

「只有供应商是定死的」就落在这里：每家供应商需要单独的 adapter 实现（不一定符合
OpenAI 规范），所以供应商集合由代码决定，用户无法在配置文件里新增供应商。
模型则完全由配置文件驱动——本文件不保存任何模型清单。

这是 LLM 领域里配置切片与调用切片*共享*的身份/能力核心：
    - 配置切片：用它校验配置文件里的供应商段是否合法。
    - 调用切片（将来）：用 provider id 作为分发键，路由到对应 adapter。
（adapter 绑定等真正做调用适配时再挂到 ProviderSpec 上，现在不预设。）

凭证不入文件：api_key_env 只是「该供应商默认从哪个环境变量取 key」的提示，
真正的 key 始终从环境变量 / keychain 读取。
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.application.llm.errors import UnknownProvider


@dataclass(frozen=True)
class ProviderSpec:
    """一家供应商的代码侧定义。"""

    id: str
    label: str  # 默认展示名（配置可覆盖 name）
    default_api_base: str  # 配置未给 api_base 时的回落
    api_key_env: str  # 默认凭证环境变量名


REGISTRY: dict[str, ProviderSpec] = {
    "deepseek": ProviderSpec(
        id="deepseek",
        label="DeepSeek",
        default_api_base="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
    ),
    # 注意：MiMo 的官方 OpenAI 兼容 base_url 请按实际填写（配置文件可覆盖）。
    "mimo": ProviderSpec(
        id="mimo",
        label="MiMo",
        default_api_base="",  # 留空：必须在配置 / adapter 里明确给出
        api_key_env="MIMO_API_KEY",
    ),
}


def is_known_provider(provider_id: str) -> bool:
    return provider_id in REGISTRY


def require_known_provider(provider_id: str) -> ProviderSpec:
    spec = REGISTRY.get(provider_id)
    if spec is None:
        allowed = " / ".join(sorted(REGISTRY))
        raise UnknownProvider(
            f"未知供应商 {provider_id!r}；只支持内置供应商 [{allowed}]，"
            f"新增供应商需要实现对应 adapter。"
        )
    return spec
