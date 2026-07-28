"""内置供应商注册表（ADR-0011 §5）。

描述词汇（ProviderSpec / ThinkingDialect）住在 domain.model.provider_spec；
这里是封闭的注册表本身：具体几家、各自的 base url 与 API key 环境变量名。
这些是部署事实而非领域概念，改一家供应商的地址不该惊动领域层。
"""

from __future__ import annotations

from forgecli.application.llm.errors import UnknownProvider
from forgecli.domain.model.provider_spec import ProviderSpec

REGISTRY: dict[str, ProviderSpec] = {
    "deepseek": ProviderSpec(
        id="deepseek",
        label="DeepSeek",
        default_api_base="https://api.deepseek.com/chat/completions",
        api_key_env="DEEPSEEK_API_KEY",
    ),
    # 注意：MiMo 的官方 OpenAI 兼容 base_url 请按实际填写（配置文件可覆盖）。
    "mimo": ProviderSpec(
        id="mimo",
        label="MiMo",
        default_api_base="",  # 留空：必须在配置 / adapter 里明确给出
        api_key_env="MIMO_API_KEY",
    ),
    # OpenAI 官方 / 兼容端点：base_url 由配置或 adapter 给出。
    "openai": ProviderSpec(
        id="openai",
        label="OpenAI",
        default_api_base="",
        api_key_env="OPENAI_API_KEY",
    ),
    "GLM": ProviderSpec(
        id="glm",
        label="GLM",
        default_api_base="https://open.bigmodel.cn/api/paas/v4/chat/completions",
        api_key_env="GLM_API_KEY",
    ),
    # 本地推理端点（Ollama / vLLM 等）：默认免凭证，api_base 由配置给出。
    "local": ProviderSpec(
        id="local",
        label="Local",
        default_api_base="",
        api_key_env="",  # 免 key；availability 对免凭证 provider 的细化见 07-03
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
