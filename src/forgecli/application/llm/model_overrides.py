"""按用途模型覆盖读取 build_model_overrides（ADR-0011 §3.4 / §16）。

把已加载的 `[model_overrides.<origin>]` 段转成类型化 {RequestOrigin: ModelRef} 覆盖表，
供 ModelSelectionResolver 注入使用。这里只做「已加载 mapping -> 类型化值」的转换与校验，
*不*做文件 IO、*不*解析 TOML（解析归 config store）；resolver 由此保持只依赖 catalog。

当前主模型（[model].provider / [model].name）的读取复用 EffectiveConfig.default_model，
本模块只负责可选的按用途覆盖段。空段 -> 空表（不生成任何「第二默认模型」）。
"""

from __future__ import annotations

from collections.abc import Mapping

from forgecli.application.llm.gateway.errors import ModelBadRequestError
from forgecli.application.llm.gateway.origin import RequestOrigin
from forgecli.application.llm.model_ref import ModelRef


def build_model_overrides(
    section: Mapping[str, Mapping[str, str]],
) -> dict[RequestOrigin, ModelRef]:
    """`[model_overrides.<origin>]` -> {RequestOrigin: ModelRef}；非法项归一为网关错误。

    校验：origin 必须是封闭 RequestOrigin 之一；每项必须同时给出非空 provider 与 model。
    """
    overrides: dict[RequestOrigin, ModelRef] = {}
    for raw_origin, body in section.items():
        origin = _parse_origin(raw_origin)
        if not isinstance(body, Mapping):
            raise ModelBadRequestError(
                f"按用途覆盖 {raw_origin!r} 必须是 provider/model 键值表"
            )
        provider = str(body.get("provider", "")).strip()
        model = str(body.get("model", "")).strip()
        if not provider or not model:
            raise ModelBadRequestError(
                f"按用途覆盖 {raw_origin!r} 必须同时指定非空 provider 与 model"
            )
        overrides[origin] = ModelRef(provider=provider, model=model)
    return overrides


def _parse_origin(raw_origin: str) -> RequestOrigin:
    try:
        return RequestOrigin(raw_origin)
    except ValueError:
        allowed = " / ".join(origin.value for origin in RequestOrigin)
        raise ModelBadRequestError(
            f"未知用途覆盖 origin: {raw_origin!r}；只支持 [{allowed}]"
        ) from None
