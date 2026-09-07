"""模型, 供应商与网关运行参数。

这几条路由共享一条纪律: **改完就重载**。配置文件与生效状态不得静默分离 —— 保存成功
却要等下次重启才生效, 用户看到的是"改了没用"(ADR-0048 决策 1)。重载本身保留会话。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from forgecli.application.llm import providers as provider_registry
from forgecli.application.llm.config.llm_config import (
    PROVIDER_FIELDS,
    STANDARD_FIELDS,
    StandardField,
)
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.provider_spec import ProviderProtocol, ProviderSpec
from forgecli.domain.model.thinking import ThinkingEffortName, ThinkingMode
from forgecli.interfaces.runtime.project_runtime import ProjectRuntime
from forgecli.interfaces.web.deps import active_runtime, idle_runtime
from forgecli.shared.errors import ConfigValidationError
from forgecli.shared.serialization import to_jsonable

router = APIRouter(prefix="/api/v1")

_CONFIG_ACTION = "修改模型配置"


class CurrentModelRequest(BaseModel):
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)


class ModelOverrideRequest(BaseModel):
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)


class ThinkingRequest(BaseModel):
    mode: str = ""
    effort: str = ""


class ProviderCreateRequest(BaseModel):
    provider_id: str = Field(min_length=1)
    name: str = ""
    api_base: str = Field(min_length=1)
    protocol: str = Field(min_length=1)
    api_key_env: str = ""


class ModelCreateRequest(BaseModel):
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    params: dict[str, object] = Field(default_factory=dict)


class LlmFieldUpdateRequest(BaseModel):
    value: str = ""


@contextmanager
def _mapped_errors() -> Iterator[None]:
    """LLM 配置写入的错误映射: 冲突 409, 其余一律 422。

    这七行原先在每条写路由里各抄一遍。抄得少一支的那条会把配置错误漏成 500, 而 500
    在页面上只剩一句"服务器错误"—— 用户看不到到底哪个字段填错了。
    """
    try:
        yield
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # 配置错误族都带可直接展示的信息
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


def _field_view(field: StandardField) -> dict[str, str]:
    """一个可编辑字段的表单描述. kind 是给前端选控件用的."""
    return {"name": field.name, "label": field.label, "kind": field.kind.__name__}


def _listed_providers(runtime: ProjectRuntime) -> list[ProviderSpec]:
    """内置的按注册表顺序, 用户自建的跟在后面, 都按 id 排序.

    自建的从**配置**里取而不是从进程内的登记表: 登记发生在读配置那一刻, 而这个路由
    可能先被调到.
    """
    builtin = [
        provider_registry.REGISTRY[key] for key in sorted(provider_registry.REGISTRY)
    ]
    custom = [
        ProviderSpec(
            id=provider.id,
            label=provider.name,
            default_api_base=provider.api_base,
            api_key_env=provider.api_key_env or "",
        )
        for provider in sorted(runtime.llm_config.providers(), key=lambda item: item.id)
        if provider.id not in provider_registry.REGISTRY
    ]
    return builtin + custom


@router.get("/models")
async def models(request: Request) -> dict[str, object]:
    runtime = active_runtime(request)
    cache, circuit_breaker, retry = runtime.llm_config.runtime_settings_snapshot()
    configured, effective = runtime.llm_config.provider_views()
    current = runtime.current_model()
    return {
        "items": [to_jsonable(item) for item in configured],
        # 每家内置供应商各一条 (没配过的带注册表默认值): 设置页要能在添加第一个模型
        # 之前就把端点填好.
        "provider_settings": [to_jsonable(item) for item in effective],
        # 供应商表单该有哪些行, 每行叫什么, 是什么类型 —— 全从 ProviderConfig
        # 的字段声明派生 (ADR-0040 决策 4.2). 页面照着渲染, 不自己列一份.
        "provider_fields": [_field_view(field) for field in PROVIDER_FIELDS],
        # 模型标准字段同样来自 ModelParams 声明；前端不再维护一份会漂移的字段表。
        "model_fields": [_field_view(field) for field in STANDARD_FIELDS],
        # 内置的与用户自建的一起发: 页面据此显示"密钥已就绪 / 缺少 XXX / 无需密钥",
        # 而自建的那几家缺席时, 页面只能自己编一个可用性 —— 编出来的那个一律是
        # "已就绪", 于是一家根本没配环境变量的供应商看起来是好的.
        "known_providers": [
            {
                "id": spec.id,
                "label": spec.label,
                "api_key_env": spec.api_key_env,
                # 只看环境变量在不在, 不读它的值 (application/llm/availability).
                "available": runtime.availability.is_available(spec.id),
                # 哪几家是内置的由后端说. 前端手抄一份清单的话, 加一家内置供应商就
                # 会让它出现在"自建"那一组下, 而不会有任何东西报错.
                "builtin": spec.id in provider_registry.REGISTRY,
            }
            for spec in _listed_providers(runtime)
        ],
        # 三种协议全都列出来, 带上 supported 标记 —— 不支持的要在界面上看得见但
        # 选不了. 只列支持的那一种, 用户会以为 Forge 不打算支持另外两种, 于是去
        # 找别的工具.
        "provider_protocols": [
            {
                "value": protocol.value,
                "label": protocol.label,
                "supported": protocol.supported,
            }
            for protocol in ProviderProtocol
        ],
        "current_model": "" if current is None else str(current),
        "overrides": runtime.model_overrides(),
        "origins": [origin.value for origin in RequestOrigin],
        "thinking": runtime.thinking_view(),
        "runtime": {
            "cache": to_jsonable(cache),
            "circuit_breaker": to_jsonable(circuit_breaker),
            "retry": to_jsonable(retry),
        },
    }


@router.put("/models/current")
async def set_current_model(
    body: CurrentModelRequest, request: Request
) -> dict[str, str]:
    """选择运行时默认模型。两个配置键一起改。"""
    runtime = idle_runtime(request, "切换模型")
    with _mapped_errors():
        runtime.set_current_model(body.provider_id, body.model_id)
    current = runtime.current_model()
    return {"current_model": "" if current is None else str(current)}


@router.put("/model-overrides/{origin}")
async def set_model_override(
    origin: str, body: ModelOverrideRequest, request: Request
) -> dict[str, object]:
    """按用途覆盖当前模型。"""
    runtime = idle_runtime(request, "修改模型覆盖")
    with _mapped_errors():
        runtime.set_model_override(origin, body.provider_id, body.model_id)
    return {"overrides": runtime.model_overrides()}


@router.delete("/model-overrides/{origin}")
async def clear_model_override(origin: str, request: Request) -> dict[str, object]:
    runtime = idle_runtime(request, "修改模型覆盖")
    with _mapped_errors():
        runtime.clear_model_override(origin)
    return {"overrides": runtime.model_overrides()}


@router.post("/thinking")
async def update_thinking(body: ThinkingRequest, request: Request) -> dict[str, object]:
    """当前模型的 thinking 开关与强度。只改本进程，不落盘。"""
    runtime = idle_runtime(request, "修改 thinking")
    with _mapped_errors():
        changed = runtime.update_thinking(body.mode, body.effort)
    return {"changed": changed, "thinking": runtime.thinking_view()}


@router.post("/models", status_code=status.HTTP_201_CREATED)
async def add_model(body: ModelCreateRequest, request: Request) -> dict[str, bool]:
    runtime = idle_runtime(request, _CONFIG_ACTION)
    with _mapped_errors():
        runtime.llm_config.add_model(body.provider_id, body.model_id, body.params)
        runtime.reload_llm()
    return {"created": True}


@router.delete("/models")
async def remove_model(
    provider_id: str, model_id: str, request: Request
) -> dict[str, bool]:
    runtime = idle_runtime(request, _CONFIG_ACTION)
    with _mapped_errors():
        runtime.llm_config.remove_model(provider_id, model_id)
        runtime.reload_llm()
    return {"removed": True}


@router.patch("/models/{field}")
async def update_model_field(
    provider_id: str,
    model_id: str,
    field: str,
    body: LlmFieldUpdateRequest,
    request: Request,
) -> dict[str, bool]:
    runtime = idle_runtime(request, _CONFIG_ACTION)
    config = runtime.llm_config
    with _mapped_errors():
        if field == "extra":
            config.set_model_extra(provider_id, model_id, body.value)
        elif field == "thinking_mode":
            config.update_model_thinking(
                provider_id, model_id, mode=ThinkingMode(body.value)
            )
        elif field == "thinking_effort":
            config.update_model_thinking(
                provider_id, model_id, effort=ThinkingEffortName(body.value)
            )
        elif field == "thinking_efforts":
            config.set_model_thinking_efforts(provider_id, model_id, body.value)
        elif field == "thinking_default_effort":
            config.set_model_thinking_default_effort(provider_id, model_id, body.value)
        else:
            config.set_model_field(provider_id, model_id, field, body.value)
        runtime.reload_llm()
    return {"updated": True}


@router.post("/providers", status_code=status.HTTP_201_CREATED)
async def add_provider(
    body: ProviderCreateRequest, request: Request
) -> dict[str, bool]:
    """加一家用户自建的供应商 (只收 OpenAI 兼容协议)."""
    runtime = idle_runtime(request, _CONFIG_ACTION)
    try:
        runtime.llm_config.add_provider(
            body.provider_id,
            name=body.name,
            api_base=body.api_base,
            protocol=body.protocol,
            api_key_env=body.api_key_env,
        )
        runtime.reload_llm()
    except ConfigValidationError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from None
    return {"created": True}


@router.patch("/providers/{provider_id}/{field}")
async def update_provider_field(
    provider_id: str,
    field: str,
    body: LlmFieldUpdateRequest,
    request: Request,
) -> dict[str, bool]:
    runtime = idle_runtime(request, _CONFIG_ACTION)
    with _mapped_errors():
        runtime.llm_config.set_provider_field(provider_id, field, body.value)
        runtime.reload_llm()
    return {"updated": True}


@router.patch("/llm-runtime/{section}/{field}")
async def update_llm_runtime_field(
    section: str,
    field: str,
    body: LlmFieldUpdateRequest,
    request: Request,
) -> dict[str, bool]:
    runtime = idle_runtime(request, "修改网关配置")
    with _mapped_errors():
        runtime.llm_config.set_runtime_field(section, field, body.value)
        runtime.reload_llm()
    return {"updated": True}
