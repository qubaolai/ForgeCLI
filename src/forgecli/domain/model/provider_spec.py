"""供应商的描述词汇（ADR-0011 §5）。

ProviderSpec 说的是一个供应商长什么样（标签、默认 base url、凭证环境变量名、
thinking 方言），ThinkingDialect 说的是它用哪种 thinking 表达。

具体有哪几家供应商不在这里——那是部署事实（写死的 URL 与环境变量名），
住在 application/llm/providers.py 的 REGISTRY。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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
