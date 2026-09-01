"""供应商的描述词汇（ADR-0011 §5）。

ProviderSpec 说的是一个供应商长什么样（标签、默认 base url、凭证环境变量名、
协议、thinking 方言），ProviderProtocol 说的是它的端点讲哪一套协议，
ThinkingDialect 说的是它用哪种 thinking 表达。

具体有哪几家供应商不在这里——那是部署事实（写死的 URL 与环境变量名），
住在 application/llm/providers.py 的 REGISTRY。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProviderProtocol(Enum):
    """供应商端点说的是哪一套协议.

    **封闭枚举, 且只有一个成员当前可用.** 另外两个写在这里不是占位 —— 它们要出现在
    界面上并且**不可选**: 用户看到"暂不支持"与看不到这一项是两回事, 后者会让人以为
    Forge 不打算支持, 于是去找别的工具.

    `supported` 而不是把不支持的删掉: 删掉之后, 一个手写配置里填了 anthropic 的用户
    得到的是"未知协议", 而那句话不解释任何事情.
    """

    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC_MESSAGES = "anthropic_messages"
    GOOGLE_GEMINI = "google_gemini"

    @property
    def label(self) -> str:
        return _PROTOCOL_LABELS[self]

    @property
    def supported(self) -> bool:
        """有没有对应的 adapter. 只有一个有, 其余两个是声明过的未来."""
        return self is ProviderProtocol.OPENAI_COMPATIBLE


_PROTOCOL_LABELS: dict[ProviderProtocol, str] = {
    ProviderProtocol.OPENAI_COMPATIBLE: "OpenAI Compatible (/chat/completions)",
    ProviderProtocol.ANTHROPIC_MESSAGES: "Anthropic Messages",
    ProviderProtocol.GOOGLE_GEMINI: "Google Gemini",
}


class ThinkingDialect(Enum):
    """provider 的 thinking 协议方言。

    EFFORT 将模型声明的 effort 名称原样发送；
    BUDGET 通过 adapter 的已知映射转换为 token budget；
    NONE 表示该 provider 协议没有可发送的 thinking 字段。
    """

    EFFORT = "effort"
    BUDGET = "budget"
    NONE = "none"


@dataclass(frozen=True)
class ProviderSpec:
    """一家供应商的代码侧定义。"""

    id: str
    label: str  # 默认展示名（配置可覆盖 name）
    default_api_base: str  # 配置未给 api_base 时的回落
    api_key_env: str  # 默认凭证环境变量名
    # thinking 方言声明（ADR-0012 §4）：wiring 据此实例化 adapter。
    thinking_dialect: ThinkingDialect = ThinkingDialect.EFFORT
    # 端点协议. 内置几家目前全是 OpenAI 兼容的; 用户自建的按它选.
    protocol: ProviderProtocol = ProviderProtocol.OPENAI_COMPATIBLE
