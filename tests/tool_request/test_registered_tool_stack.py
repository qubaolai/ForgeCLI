"""组合根装配出来的那套工具, 走完整条协调器链路 (ADR-0004 §13).

这里补的是一个真实存在过的空档: 每个工具都有自己的单测, 但**注册表**没有任何测试.
于是"实现了一个工具"和"模型能调到它"之间没有任何东西负责对齐 —— 新写的工具忘了加进
build_tool_stack, 一行代码都不会报错, 只有真跑一轮才发现模型看不见它.

模型请求一个不存在的 fs_write_file 就是这条缝的后果之一: 它想建文件, 工具表里找不到
叫"建文件"的东西, 自己编了一个名字, 拿回"未注册", 转头去用 shell 写 —— 绕开了恢复层
与逐次裁决. 所以这里同时钉住两件事: 常用动作各自都有专用工具, 以及它们真的接上了.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.application.llm.gateway.gateway import LlmGateway
from forgecli.application.session.session_service import SessionService
from forgecli.application.tool_request.observations import ObservationKind
from forgecli.domain.agent.actions import ToolRequest
from forgecli.domain.intents import SessionMode
from forgecli.domain.model.request import ModelRequest, StructuredModelRequest
from forgecli.domain.model.response import ModelResponse, StructuredModelResponse
from forgecli.domain.model.streaming import ModelStreamChunk
from forgecli.domain.security.context import PolicyContext
from forgecli.infrastructure.session.json_state_store import JsonStateStore
from forgecli.infrastructure.session.jsonl_event_store import JsonlEventStore
from forgecli.interfaces.runtime.tool_wiring import ToolStack, build_tool_stack

# 一个动作一个工具. 左边是用户会说的话, 右边是模型在工具表里能找到的名字 —— 两者对不
# 上的时候, 模型不会退回去问, 它会自己编一个名字或者改用 shell.
EXPECTED_TOOLS = {
    "定位文件与认识目录": "fs_find",
    "读取文件": "fs_read",
    "搜索内容": "search_text",
    "读取 git": "git_read",
    "改文件 (新建/更新/删除/移动)": "fs_apply_patch",
    "执行命令": "shell_run",
}


class UnusedGateway(LlmGateway):
    """本用例不调模型: 分类器只在脚本执行路径上才会被问到."""

    def complete(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("本用例不该调用模型")

    def complete_structured(
        self, request: StructuredModelRequest
    ) -> StructuredModelResponse:
        raise AssertionError("本用例不该调用模型")

    def stream(self, request: ModelRequest) -> Iterator[ModelStreamChunk]:
        raise AssertionError("本用例不该调用模型")


@pytest.fixture
def stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ToolStack:
    # 恢复数据与产物落在 Forge home, 绝不能进工作区 (ADR-0015 §6).
    home = tmp_path / "forge-home"
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(home))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sessions = home / "sessions"
    session = SessionService(
        JsonlEventStore(sessions),
        JsonStateStore(sessions),
        workspace_root=str(workspace),
    )
    session.start()
    return build_tool_stack(
        workspace_roots=(str(workspace),),
        workspace_id="ws-integration",
        session=session,
        gateway=UnusedGateway(),
        run_bus=AgentRunEventBus(),
    )


def _call(stack: ToolStack, name: str, **arguments: object):  # type: ignore[no-untyped-def]
    return stack.coordinator.handle(
        ToolRequest(name=name, arguments=arguments),
        context=replace(
            stack.context_factory(),
            fence=stack.fence_factory(SessionMode.ACCEPT_EDITS),
        ),
        policy=PolicyContext(
            mode=SessionMode.ACCEPT_EDITS,
            session_id="sess-1",
            turn_id="turn-1",
            execution_profile_hash=stack.profile.execution_profile_hash,
            fence=stack.fence_factory(SessionMode.ACCEPT_EDITS),
            confined=stack.confined,
        ),
    )


def test_every_common_action_has_a_registered_tool(stack: ToolStack) -> None:
    missing = {
        action: name
        for action, name in EXPECTED_TOOLS.items()
        if not stack.registry.contains(name)
    }
    assert not missing, f"这些动作没有专用工具, 模型只能自己想办法: {missing}"


def test_the_model_sees_the_new_tools_in_its_catalog(stack: ToolStack) -> None:
    """注册了但没进目录等于没有: 模型看到的是目录, 不是注册表."""
    catalog = stack.coordinator.catalog_for(
        PolicyContext(
            mode=SessionMode.ACCEPT_EDITS,
            session_id="sess-1",
            turn_id="turn-1",
            execution_profile_hash=stack.profile.execution_profile_hash,
        )
    )
    for name in ("fs_find", "fs_read", "fs_apply_patch"):
        assert catalog.contains(name), f"{name} 不在模型看得到的目录里"


def test_creating_then_editing_a_file_goes_through_the_whole_pipeline(
    stack: ToolStack, tmp_path: Path
) -> None:
    """建 -> 改 -> 读: 三次都要真的落盘, 而且走的是裁决与恢复那条链路."""
    target = tmp_path / "ws" / "notes.txt"

    created = _call(stack, "fs_apply_patch", patch="*** NEW notes.txt\nv = 1\n")
    assert created.kind is ObservationKind.TOOL_RESULT
    assert target.read_text(encoding="utf-8") == "v = 1\n"

    edited = _call(
        stack,
        "fs_apply_patch",
        patch="*** UPDATE notes.txt\n*** FIND\nv = 1\n*** REPLACE\nv = 2",
    )
    assert edited.kind is ObservationKind.TOOL_RESULT
    assert target.read_text(encoding="utf-8") == "v = 2\n"

    read = _call(stack, "fs_read", path="notes.txt")
    assert read.kind is ObservationKind.TOOL_RESULT
    assert read.result is not None
    assert "v = 2" in read.result.text


def test_creating_over_an_existing_file_fails_and_says_which_tool_to_use(
    stack: ToolStack,
) -> None:
    _call(stack, "fs_apply_patch", patch="*** NEW a.txt\n原内容\n")

    again = _call(stack, "fs_apply_patch", patch="*** NEW a.txt\n覆盖\n")

    assert again.kind is ObservationKind.PREPARATION_FAILED
    assert "UPDATE" in again.message


def test_scanning_the_workspace_returns_a_tree(stack: ToolStack) -> None:
    _call(stack, "fs_apply_patch", patch="*** NEW src/main.py\nprint(1)\n")

    scanned = _call(stack, "fs_find")

    assert scanned.kind is ObservationKind.TOOL_RESULT
    assert scanned.result is not None
    tree = scanned.result.text
    assert "src/" in tree
    assert "  main.py" in tree


def test_a_tool_name_the_model_made_up_still_leaves_a_terminal_trace(
    stack: ToolStack,
) -> None:
    """回归: fs_write_file 曾经悄悄结束 —— 模型拿到了结论, 页面上却永远停在"未完成"."""
    observation = _call(stack, "fs_write_file", path="a.txt", content="x")

    assert observation.kind is ObservationKind.TOOL_UNAVAILABLE
    assert observation.reason_code == "tool_unavailable"


# ---- 真实日志里被卡住的那几条 ----


def test_a_multi_file_envelope_lands_every_section(
    stack: ToolStack, tmp_path: Path
) -> None:
    """两段共用一个新建祖先目录时, 整封补丁只写进了第一个文件.

    每段的父目录清单是 prepare 阶段按各自执行前的磁盘状态算的, 而落盘那一步
    mkdir(exist_ok=False). 真实日志里 29 次调用中 11 次这样半途停下.
    """
    result = _call(
        stack,
        "fs_apply_patch",
        patch=(
            "*** NEW backend/pom.xml\n<project/>\n\n"
            "*** NEW backend/src/Main.java\nclass Main {}\n\n"
            "*** NEW backend/src/App.java\nclass App {}"
        ),
    )

    assert result.kind is ObservationKind.TOOL_RESULT
    workspace = tmp_path / "ws"
    assert (workspace / "backend/pom.xml").exists()
    assert (workspace / "backend/src/Main.java").exists()
    assert (workspace / "backend/src/App.java").exists()


def test_a_failed_write_tells_the_model_what_went_wrong(
    stack: ToolStack, tmp_path: Path
) -> None:
    """失败回给模型的曾经是空字符串: 只填 error 不填 content_parts."""
    (tmp_path / "ws" / "a.txt").write_text("x", encoding="utf-8")

    result = _call(stack, "fs_apply_patch", patch="*** NEW a.txt\ny")

    assert result.render().strip()


def test_an_invented_patch_verb_is_reported_not_written(
    stack: ToolStack, tmp_path: Path
) -> None:
    """`*** INSERT` 曾经作为正文第一行写进 schema.sql, 而工具回"已应用 1 处改动"."""
    result = _call(
        stack,
        "fs_apply_patch",
        patch="*** NEW schema.sql\n*** INSERT\nCREATE TABLE t (id int);",
    )

    assert result.kind is ObservationKind.PREPARATION_FAILED
    assert "*** INSERT" in result.render()
    assert not (tmp_path / "ws" / "schema.sql").exists()


def test_a_read_only_probe_with_dev_null_runs_unattended(stack: ToolStack) -> None:
    """`2>/dev/null` 曾经命中"写入设备路径"这条不可覆盖的底线.

    这是日志里那条查 maven 装在哪的命令, 它被硬拒之后模型就再也没法验证构建.
    """
    result = _call(
        stack,
        "shell_run",
        command="ls -a . 2>/dev/null; echo done",
    )

    assert result.kind is ObservationKind.TOOL_RESULT
