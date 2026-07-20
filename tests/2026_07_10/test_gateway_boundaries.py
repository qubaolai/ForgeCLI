"""ADR-0011 §19 边界验收：SDK 隔离、无凭证不发网络、gateway 不落盘。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import forgecli

_SRC_ROOT = Path(forgecli.__file__).parent


def _py_files(*parts: str) -> list[Path]:
    return sorted((_SRC_ROOT.joinpath(*parts)).rglob("*.py"))


def _imports(path: Path, module: str) -> bool:
    """是否存在对 module 的真实 import 语句（不匹配注释 / docstring 文字）。"""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith((f"import {module}", f"from {module}")):
            return True
    return False


def test_application_never_imports_http_sdk() -> None:
    """application 与 AgentTurn 不 import httpx / 供应商 SDK（§19）。"""
    offenders = [path for path in _py_files("application") if _imports(path, "httpx")]
    assert offenders == []


def test_gateway_slice_never_imports_infrastructure_or_session() -> None:
    """gateway 切片不依赖 infrastructure / session 存储（§2 边界）。"""
    for path in _py_files("application", "llm", "gateway"):
        assert not _imports(path, "forgecli.infrastructure"), path
        assert not _imports(path, "forgecli.application.session"), path


def test_httpx_confined_to_infrastructure_adapters() -> None:
    """httpx 只出现在 infrastructure 的 adapter 实现里。"""
    users = [
        path.relative_to(_SRC_ROOT) for path in _py_files() if _imports(path, "httpx")
    ]
    assert users == [Path("infrastructure/llm/adapters/openai_compatible.py")]


def test_real_wiring_without_credentials_never_hits_network(tmp_path: Path) -> None:
    """经真实装配（OpenAI adapter + 凭证池）调用：未配置凭证 -> 认证错误，
    在发起任何网络请求之前返回（§7 / §19）。"""
    from forgecli.application.config.config_service import ConfigService
    from forgecli.application.llm.config.llm_config_service import LlmConfigService
    from forgecli.application.llm.gateway import (
        ChatMessage,
        CurrentModelSelection,
        ModelAuthError,
        ModelParams,
        ModelRequest,
        RequestOrigin,
        TextBlock,
    )
    from forgecli.domain.conversation import MessageRole
    from forgecli.infrastructure.config.toml_store import TomlConfigStore
    from forgecli.infrastructure.llm import TomlLlmConfigStore
    from forgecli.interfaces.cli.llm_wiring import build_llm_runtime

    llm_toml = tmp_path / "llm.toml"
    llm_toml.write_text(
        "[llm.providers.deepseek.models.deepseek-chat]\ncontext_window = 65536\n",
        encoding="utf-8",
    )
    forge_toml = tmp_path / "forge.toml"
    forge_toml.write_text(
        '[model]\nprovider = "deepseek"\nname = "deepseek-chat"\n', encoding="utf-8"
    )
    config_service = ConfigService(
        TomlConfigStore(tmp_path / "config.toml"), TomlConfigStore(forge_toml)
    )
    llm_service = LlmConfigService(TomlLlmConfigStore(llm_toml))
    runtime = build_llm_runtime(config_service, llm_service, forge_toml)

    request = ModelRequest(
        request_id="req_1",
        session_id="sess_1",
        turn_id="turn_0001",
        origin=RequestOrigin.CHAT,
        model_selection=CurrentModelSelection(),
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock("hi"),)),),
        params=ModelParams(),
    )
    if os.environ.get("DEEPSEEK_API_KEY"):
        pytest.skip("本机设置了 DEEPSEEK_API_KEY，跳过无凭证断言")
    with pytest.raises(ModelAuthError):
        runtime.gateway.complete(request)


def test_gateway_call_writes_no_files(tmp_path: Path) -> None:
    """gateway 调用不直接写事件 / state / usage 文件（§8 / §19）。"""
    from forgecli.application.llm.gateway import (
        ChatMessage,
        CurrentModelSelection,
        DefaultLlmGateway,
        DefaultModelSelectionResolver,
        InMemoryModelCatalog,
        ModelCatalogEntry,
        ModelParams,
        ModelRequest,
        ProviderRegistry,
        RequestOrigin,
        TextBlock,
    )
    from forgecli.application.llm.model_ref import ModelRef
    from forgecli.domain.conversation import MessageRole
    from forgecli.infrastructure.llm.adapters import FakeModelProvider

    workdir = tmp_path / "observe"
    workdir.mkdir()
    registry = ProviderRegistry()
    registry.register(FakeModelProvider(content="ok"))
    catalog = InMemoryModelCatalog(
        (
            ModelCatalogEntry(
                provider="deepseek", model="deepseek-chat", context_window=65536
            ),
        )
    )
    gateway = DefaultLlmGateway(
        registry,
        resolver=DefaultModelSelectionResolver(
            catalog, current_model=ModelRef(provider="deepseek", model="deepseek-chat")
        ),
    )
    old_cwd = os.getcwd()
    os.chdir(workdir)
    try:
        gateway.complete(
            ModelRequest(
                request_id="req_1",
                session_id="sess_1",
                turn_id="turn_0001",
                origin=RequestOrigin.CHAT,
                model_selection=CurrentModelSelection(),
                messages=(
                    ChatMessage(role=MessageRole.USER, content=(TextBlock("hi"),)),
                ),
                params=ModelParams(),
            )
        )
    finally:
        os.chdir(old_cwd)
    assert list(workdir.iterdir()) == []
