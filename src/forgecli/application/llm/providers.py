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
    "glm": ProviderSpec(
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


def normalize_provider_id(raw: str) -> str:
    """外部写来的 provider id 归一成注册表的 key.

    为什么需要这一步: 这张表的 key 曾经是 `"GLM"` 而它的 `spec.id` 是 `"glm"`, 两者
    不一致了一段时间. 期间菜单是按 key 列的, 所以用户配置文件里可能真的存着 `"GLM"`,
    而代码里按 `spec.id` 查的地方一律查不到 —— `is_known_provider("glm")` 返回 False.
    key 已经改回 `"glm"`, 但已经写出去的配置不会自己改, 所以入口这里认大小写.

    只归一大小写和首尾空白, 不做别名映射: 别名是一张会长大的表, 而这里要修的只是
    一次拼写事故.
    """
    return raw.strip().lower()


# 用户自建的供应商 (ADR-0011 §5 的补充): 只要端点讲 OpenAI 兼容协议, 加一家就不需要
# 写任何代码 —— adapter 已经在那儿了, 缺的只是一条注册.
#
# 与 REGISTRY 分开而不是混进去: REGISTRY 是**代码事实** (内置几家, 各自的默认端点与
# 环境变量名), 它随版本走; 这一份是**用户事实**, 随配置文件走. 混在一起之后, "为什么
# 我的配置在另一台机器上不见了"就没有答案了.
_USER_PROVIDERS: dict[str, ProviderSpec] = {}


def register_user_provider(spec: ProviderSpec) -> None:
    """登记一家用户自建的供应商. 由配置加载时调用, 覆盖同名的上一次登记.

    不允许盖掉内置的: 内置那几家的默认端点是代码事实, 用户要改端点走配置里的
    `api_base`, 而不是重新定义这家供应商是什么.
    """
    provider_id = normalize_provider_id(spec.id)
    if provider_id in REGISTRY:
        raise UnknownProvider(f"{spec.id!r} 是内置供应商, 改端点请编辑它的 api_base")
    _USER_PROVIDERS[provider_id] = spec


def forget_user_providers() -> None:
    """清空用户自建登记. 配置重载与测试用."""
    _USER_PROVIDERS.clear()


def is_known_provider(provider_id: str) -> bool:
    normalized = normalize_provider_id(provider_id)
    return normalized in REGISTRY or normalized in _USER_PROVIDERS


def require_known_provider(provider_id: str) -> ProviderSpec:
    normalized = normalize_provider_id(provider_id)
    spec = REGISTRY.get(normalized) or _USER_PROVIDERS.get(normalized)
    if spec is None:
        allowed = " / ".join(sorted({*REGISTRY, *_USER_PROVIDERS}))
        raise UnknownProvider(
            f"未知供应商 {provider_id!r}；已知的有 [{allowed}]。"
            f"要加一家讲 OpenAI 兼容协议的, 在设置页的供应商标签里添加。"
        )
    return spec
