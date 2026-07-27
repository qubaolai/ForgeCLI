"""精确分词器接入点 TokenizerRegistry（ADR-0012 §6，补全 ADR-0011 §11.4）。

按 provider/model 前缀选择精确分词器；精确分词器作为可选依赖（extras）接入：
factory 惰性构造，import 失败（依赖未安装）静默回落 ApproximateTokenEstimator，
不影响默认安装的无网络测试。估算端口（TokenEstimator）不变——gateway 经本
registry 按已解析 ref 取估算器，调用方无感知。

匹配规则：同 provider 下取能匹配 ref.model 的最长 model_prefix（空前缀可作
provider 级默认）；无匹配回落近似估算器。构造成功 / 回落结果都会被缓存，
避免每次调用重复 import 尝试。
"""

from __future__ import annotations

from collections.abc import Callable

from forgecli.application.llm.gateway.token_estimator import (
    ApproximateTokenEstimator,
    TokenEstimator,
)
from forgecli.domain.model.model_ref import ModelRef


class TokenizerRegistry:
    """provider/model 前缀 -> 精确分词器工厂的运行时注册表。"""

    def __init__(self, *, fallback: TokenEstimator | None = None) -> None:
        self._fallback = fallback or ApproximateTokenEstimator()
        # provider -> [(model_prefix, factory)]；estimator 按 (provider, prefix) 缓存。
        self._factories: dict[str, list[tuple[str, Callable[[], TokenEstimator]]]] = {}
        self._cache: dict[tuple[str, str], TokenEstimator] = {}

    @property
    def fallback(self) -> TokenEstimator:
        """无匹配或依赖缺失时使用的近似估算器。"""
        return self._fallback

    def register(
        self,
        provider: str,
        model_prefix: str,
        factory: Callable[[], TokenEstimator],
    ) -> None:
        """注册一个精确分词器工厂；model_prefix 为空表示 provider 级默认。"""
        if not provider.strip():
            raise ValueError("TokenizerRegistry.register 的 provider 不能为空")
        self._factories.setdefault(provider, []).append((model_prefix, factory))

    def estimator_for(self, ref: ModelRef) -> TokenEstimator:
        """按已解析 ref 取估算器；无匹配或工厂 import 失败回落近似估算。"""
        candidates = self._factories.get(ref.provider, [])
        best: tuple[str, Callable[[], TokenEstimator]] | None = None
        for prefix, factory in candidates:
            if ref.model.startswith(prefix) and (
                best is None or len(prefix) > len(best[0])
            ):
                best = (prefix, factory)
        if best is None:
            return self._fallback
        key = (ref.provider, best[0])
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            estimator = best[1]()
        except ImportError:
            # 可选依赖缺失：静默回落近似估算（ADR-0012 §6），并缓存回落结果。
            estimator = self._fallback
        self._cache[key] = estimator
        return estimator
