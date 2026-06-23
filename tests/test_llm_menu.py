"""/config 下「供应商配置」「模型配置」子菜单的交互测试。

通过直接调用 Choice 的 on_text / on_select / submenu（presenter 会做的事），
验证：菜单只呈现、所有改动经 ModelsService 落盘、增删后列表就地刷新、
标准字段与 JSON 扩展字段可编辑、非法输入转一行友好提示。
"""

from __future__ import annotations

from pathlib import Path

from forgecli.application.interaction_ports import UserOutput
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.menu import Choice, Menu
from forgecli.infrastructure.llm import TomlLlmConfigStore
from forgecli.interfaces.cli.menus.llm_menu import LlmMenu


class _RecordingOutput(UserOutput):
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, message: str) -> None:
        self.lines.append(message)


def _find(menu: Menu, label: str) -> Choice:
    return next(c for c in menu.choices if c.label == label)


def _menu(tmp_path: Path) -> tuple[LlmMenu, _RecordingOutput, LlmConfigService]:
    service = LlmConfigService(TomlLlmConfigStore(tmp_path / ".forge" / "llm.toml"))
    output = _RecordingOutput()
    return LlmMenu(service, output), output, service


# ---- 供应商配置 ----


def test_providers_menu_lists_closed_set(tmp_path: Path) -> None:
    menu, _, _ = _menu(tmp_path)
    labels = {c.label for c in menu.providers_menu().choices}
    assert labels == {"DeepSeek", "MiMo"}


def test_edit_provider_api_base_persists(tmp_path: Path) -> None:
    menu, _, service = _menu(tmp_path)
    deepseek = _find(menu.providers_menu(), "DeepSeek")
    detail = deepseek.submenu()

    api_base = _find(detail, "API 地址")
    assert api_base.preview() == "https://api.deepseek.com"  # 注册表默认
    api_base.on_text("https://custom.deepseek.local/v1")

    assert (
        service.config().provider("deepseek").api_base
        == "https://custom.deepseek.local/v1"
    )


def test_edit_provider_timeout_rejects_non_int(tmp_path: Path) -> None:
    menu, output, _ = _menu(tmp_path)
    detail = _find(menu.providers_menu(), "DeepSeek").submenu()
    _find(detail, "超时(秒)").on_text("abc")
    assert output.lines and "整数" in output.lines[0]


# ---- 模型配置：增 / 改 / 删 ----


def test_add_model_then_appears_in_list(tmp_path: Path) -> None:
    menu, _, service = _menu(tmp_path)
    models = _find(menu.models_menu(), "DeepSeek").submenu()

    _find(models, "添加模型").on_text("deepseek-chat")

    # 重新构建列表（presenter 每次渲染会重建）-> 新模型在列表里
    refreshed = _find(menu.models_menu(), "DeepSeek").submenu()
    assert any(c.label == "deepseek-chat" for c in refreshed.choices)
    assert service.config().model("deepseek", "deepseek-chat") is not None


def test_edit_standard_field(tmp_path: Path) -> None:
    menu, _, service = _menu(tmp_path)
    service.add_model("deepseek", "deepseek-chat", {})
    models = _find(menu.models_menu(), "DeepSeek").submenu()
    detail = _find(models, "deepseek-chat").submenu()

    _find(detail, "上下文窗口").on_text("65536")
    _find(detail, "温度").on_text("0.7")

    params = service.config().model("deepseek", "deepseek-chat").params
    assert params.context_window == 65536
    assert params.temperature == 0.7


def test_edit_standard_field_out_of_range_is_reported(tmp_path: Path) -> None:
    menu, output, service = _menu(tmp_path)
    service.add_model("deepseek", "m", {})
    detail = _find(_find(menu.models_menu(), "DeepSeek").submenu(), "m").submenu()

    _find(detail, "温度").on_text("9")  # 超出 [0,2]

    assert output.lines and "temperature" in output.lines[0]


def test_edit_extra_json(tmp_path: Path) -> None:
    menu, output, service = _menu(tmp_path)
    service.add_model("deepseek", "deepseek-reasoner", {})
    detail = _find(
        _find(menu.models_menu(), "DeepSeek").submenu(), "deepseek-reasoner"
    ).submenu()

    extra = _find(detail, "扩展字段(JSON)")
    extra.on_text('{"reasoning": true, "budget": 2048}')

    params = service.config().model("deepseek", "deepseek-reasoner").params
    assert params.extra == {"reasoning": True, "budget": 2048}
    # 展示回单行 JSON，可作为再次编辑的初值
    assert "reasoning" in extra.text_default()
    assert not output.lines


def test_edit_extra_invalid_json_is_reported(tmp_path: Path) -> None:
    menu, output, service = _menu(tmp_path)
    service.add_model("deepseek", "m", {})
    detail = _find(_find(menu.models_menu(), "DeepSeek").submenu(), "m").submenu()

    _find(detail, "扩展字段(JSON)").on_text("{not json}")

    assert output.lines and "JSON" in output.lines[0]


def test_delete_model_refreshes_list(tmp_path: Path) -> None:
    menu, _, service = _menu(tmp_path)
    service.add_model("deepseek", "a", {})
    service.add_model("deepseek", "b", {})

    delete_menu = _find(
        _find(menu.models_menu(), "DeepSeek").submenu(), "删除模型"
    ).submenu()
    _find(delete_menu, "删除 a").on_select()

    assert service.config().model("deepseek", "a") is None
    # 重新构建删除菜单 -> a 不再出现
    refreshed = _find(
        _find(menu.models_menu(), "DeepSeek").submenu(), "删除模型"
    ).submenu()
    assert {c.label for c in refreshed.choices} == {"删除 b"}


def test_model_detail_handles_deleted_model(tmp_path: Path) -> None:
    menu, _, service = _menu(tmp_path)
    service.add_model("deepseek", "gone", {})
    detail_builder = _find(
        _find(menu.models_menu(), "DeepSeek").submenu(), "gone"
    ).submenu

    service.remove_model("deepseek", "gone")

    # presenter 下次渲染会重建该层；不应抛异常，给出占位提示
    rebuilt = detail_builder()
    assert rebuilt.choices[0].label.startswith("（模型不存在")
