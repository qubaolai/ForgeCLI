"""fs_apply_patch: 一个信封替掉五个写工具 (ADR-0029 C 类).

替代了 `test_fs_write_and_delete.py` 与 `test_fs_move.py`. 覆盖的行为一条不减, 但两处
判据随合并发生了**有意的**变化, 各有一条用例专门钉住:

- `fs_create_directory` 没有对应的段: `*** NEW` 按需建父目录.
- 删目录不再需要 `recursive=true`: 信封里路径是明写的, 展开后的清单进审批与恢复层,
  再要一个布尔开关买不到新信息.

三件事仍然在 prepare 阶段定死: 要写的内容, 要删的清单, 要移的两端都是**计划里的事实**,
不是执行时才算的. 裁决, 审批与恢复层拿到的因此是"文件会变成什么样".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.application.tools.builtin.fs_apply_patch import ApplyPatchTool
from forgecli.application.tools.builtin.fs_read import ReadFileTool
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.tool import ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.errors import PreparationError
from forgecli.domain.tool.plan import TargetResolution, ToolPlan
from forgecli.domain.tool.result import ToolResultStatus
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView
from support.fakes import PROFILE


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root


def _context(workspace: Path) -> ExecutionContext:
    return ExecutionContext(
        cwd=str(workspace),
        workspace_roots=(str(workspace),),
        environment={"PATH": "/usr/bin:/bin"},
        filesystem=OsFileSystemView(),
        profile=PROFILE,
    )


def _tool() -> ApplyPatchTool:
    return ApplyPatchTool(
        lambda path, content: Path(path).write_text(content, encoding="utf-8"),
        lambda path, content: Path(path).write_text(content, encoding="utf-8"),
        lambda path: (
            Path(path).rmdir() if Path(path).is_dir() else Path(path).unlink()
        ),
        lambda source, target: Path(source).rename(target),
        lambda path: Path(path).mkdir(parents=True, exist_ok=True),
    )


def _prepare(workspace: Path, patch: str) -> ToolPlan | PreparationError:
    return _tool().prepare(
        ToolInvocationRequest(
            invocation_id="inv-1",
            tool_name="fs_apply_patch",
            arguments={"patch": patch},
            tool_call_id="c1",
        ),
        _context(workspace),
    )


def _operations(plan: ToolPlan) -> tuple[dict[str, object], ...]:
    raw = plan.normalized_input["operations"]
    assert isinstance(raw, tuple)
    return tuple(dict(item) for item in raw)


# ---- 新建 ----


def test_creating_a_file_carries_the_whole_content(workspace: Path) -> None:
    plan = _prepare(workspace, "*** NEW a.py\nprint(1)\n")

    assert isinstance(plan, ToolPlan)
    assert _operations(plan)[0]["content"] == "print(1)\n"
    assert plan.effects.write_paths == (str(workspace / "a.py"),)


def test_the_plan_shows_what_the_file_will_contain(workspace: Path) -> None:
    """审批界面逐字展示最终形态 —— 内容已由 normalized_input 绑定, 预览只是交出来."""
    plan = _prepare(workspace, "*** NEW a.py\nhello\n")

    assert isinstance(plan, ToolPlan)
    assert [preview.content for preview in plan.content_previews] == ["hello\n"]


def test_creating_over_an_existing_file_is_refused(workspace: Path) -> None:
    """新建不覆盖. 错误必须指向 UPDATE, 否则模型只会原地重试."""
    (workspace / "a.py").write_text("old", encoding="utf-8")

    error = _prepare(workspace, "*** NEW a.py\nnew\n")

    assert isinstance(error, PreparationError)
    assert "已存在" in error.message
    assert "UPDATE" in error.message


def test_a_new_file_creates_its_missing_parents(workspace: Path) -> None:
    """`fs_create_directory` 因此不需要存在 (ADR-0029 C 类).

    建父目录是"新建文件"这个动作的一部分, 单独占一个工具位换不来任何东西. 目录逐个
    进 write_paths —— 空目录同样是需要恢复的用户状态.
    """
    plan = _prepare(workspace, "*** NEW pkg/deep/a.py\nx\n")

    assert isinstance(plan, ToolPlan)
    assert plan.effects.write_paths == (
        str(workspace / "pkg"),
        str(workspace / "pkg" / "deep"),
        str(workspace / "pkg" / "deep" / "a.py"),
    )


def test_a_new_file_gets_its_trailing_newline_back(workspace: Path) -> None:
    """信封语法把末尾换行当分隔符, 而绝大多数文本文件以换行结尾.

    补上并在结果里说明, 好过让每个新建文件都缺一个换行 —— 那种缺失 diff 里看得见,
    但模型不会想到.
    """
    plan = _prepare(workspace, "*** NEW a.py\nx = 1")

    assert isinstance(plan, ToolPlan)
    operation = _operations(plan)[0]
    assert operation["content"] == "x = 1\n"
    assert "末尾换行" in str(operation["note"])


# ---- 更新 ----


def test_a_unique_fragment_is_replaced(workspace: Path) -> None:
    (workspace / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")

    plan = _prepare(workspace, "*** UPDATE a.py\n*** FIND\nx = 1\n*** REPLACE\nx = 9")

    assert isinstance(plan, ToolPlan)
    assert _operations(plan)[0]["content"] == "x = 9\ny = 2\n"


def test_several_replacements_apply_in_order(workspace: Path) -> None:
    """逐处顺序施加, 每处在上一处的结果上定位.

    在原始内容上各自算偏移再拼接会错位 —— 同一个文件里两处替换可能互相靠近.
    """
    (workspace / "a.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")

    plan = _prepare(
        workspace,
        "*** UPDATE a.py\n"
        "*** FIND\na = 1\n*** REPLACE\na = 10\n"
        "*** FIND\nc = 3\n*** REPLACE\nc = 30",
    )

    assert isinstance(plan, ToolPlan)
    assert _operations(plan)[0]["content"] == "a = 10\nb = 2\nc = 30\n"


def test_updating_a_missing_file_points_at_new(workspace: Path) -> None:
    error = _prepare(workspace, "*** UPDATE gone.py\n*** FIND\nx\n*** REPLACE\ny")

    assert isinstance(error, PreparationError)
    assert "不存在" in error.message
    assert "NEW" in error.message


def test_a_fragment_that_does_not_exist_is_rejected(workspace: Path) -> None:
    (workspace / "a.py").write_text("x = 1\n", encoding="utf-8")

    error = _prepare(workspace, "*** UPDATE a.py\n*** FIND\nnowhere\n*** REPLACE\nz")

    assert isinstance(error, PreparationError)
    assert "找不到" in error.message


def test_the_error_says_which_section_and_which_replacement(workspace: Path) -> None:
    """ADR-0029 约束 3: 解析或定位失败要指到**哪一段的哪一处**, 不整封退回.

    一封补丁里第三处替换失败时, 模型需要知道是第三处 —— 否则它只能整封重写, 而重写的
    那份里前两处大概率又换了个写法.
    """
    (workspace / "a.py").write_text("a = 1\nb = 2\n", encoding="utf-8")

    error = _prepare(
        workspace,
        "*** NEW b.py\nx\n"
        "*** UPDATE a.py\n"
        "*** FIND\na = 1\n*** REPLACE\na = 9\n"
        "*** FIND\nnowhere\n*** REPLACE\nz",
    )

    assert isinstance(error, PreparationError)
    assert "第 2 段" in error.message
    assert "第 2 处" in error.message


def test_a_file_larger_than_the_read_limit_is_refused(workspace: Path) -> None:
    """读到的是残缺前缀, 而替换结果要整体写回 —— 那会把文件截断."""
    from forgecli.application.tools.builtin.patch_apply import _MAX_SOURCE_BYTES

    big = workspace / "big.txt"
    big.write_text("x" * (_MAX_SOURCE_BYTES + 1), encoding="utf-8")

    error = _prepare(workspace, "*** UPDATE big.txt\n*** FIND\nx\n*** REPLACE\ny")

    assert isinstance(error, PreparationError)
    assert "残缺前缀" in error.message


# ---- 删除 ----


def test_deleting_a_file_targets_that_file(workspace: Path) -> None:
    (workspace / "a.py").write_text("x", encoding="utf-8")

    plan = _prepare(workspace, "*** DELETE a.py")

    assert isinstance(plan, ToolPlan)
    assert plan.effects.delete_paths == (str(workspace / "a.py"),)
    assert plan.target_resolution is TargetResolution.STATIC


def test_deleting_a_directory_expands_to_every_file(workspace: Path) -> None:
    """展开后的清单进审批与恢复层, 所以不再需要 recursive 开关 (ADR-0029).

    展开是 Forge 用冻结视图做的, 不是工具的静态声明 —— 因此是 FORGE_EXPANDED.
    """
    tree = workspace / "pkg"
    (tree / "sub").mkdir(parents=True)
    (tree / "a.py").write_text("x", encoding="utf-8")
    (tree / "sub" / "b.py").write_text("y", encoding="utf-8")

    plan = _prepare(workspace, "*** DELETE pkg")

    assert isinstance(plan, ToolPlan)
    assert str(tree / "a.py") in plan.effects.delete_paths
    assert str(tree / "sub" / "b.py") in plan.effects.delete_paths
    assert str(tree / "sub") in plan.effects.delete_paths
    assert plan.target_resolution is TargetResolution.FORGE_EXPANDED
    assert _tool().spec.target_declaration_ability.permits(plan.target_resolution)


def test_deleting_a_missing_path_is_rejected(workspace: Path) -> None:
    error = _prepare(workspace, "*** DELETE gone.py")

    assert isinstance(error, PreparationError)
    assert "不存在" in error.message


def test_a_symlink_inside_the_tree_stops_the_delete(workspace: Path) -> None:
    """递归删除遇到符号链接一律拒绝: 跟随它会删到树外面去."""
    tree = workspace / "pkg"
    tree.mkdir()
    (tree / "a.py").write_text("x", encoding="utf-8")
    (workspace / "outside.txt").write_text("keep", encoding="utf-8")
    (tree / "link").symlink_to(workspace / "outside.txt")

    error = _prepare(workspace, "*** DELETE pkg")

    assert isinstance(error, PreparationError)
    assert "符号链接" in error.message


# ---- 移动 ----


def test_moving_records_both_ends(workspace: Path) -> None:
    (workspace / "a.py").write_text("x", encoding="utf-8")

    plan = _prepare(workspace, "*** MOVE a.py -> b.py")

    assert isinstance(plan, ToolPlan)
    assert plan.effects.move_pairs[0].source == str(workspace / "a.py")
    assert plan.effects.move_pairs[0].target == str(workspace / "b.py")
    assert Capability.PATH_MOVE in plan.capabilities


def test_moving_onto_an_existing_target_is_refused(workspace: Path) -> None:
    """不静默覆盖: 覆盖是两次写, 与"移动"是两回事, 恢复层存的 preimage 也不一样."""
    (workspace / "a.py").write_text("x", encoding="utf-8")
    (workspace / "b.py").write_text("y", encoding="utf-8")

    error = _prepare(workspace, "*** MOVE a.py -> b.py")

    assert isinstance(error, PreparationError)
    assert "不会覆盖" in error.message


def test_moving_requires_an_existing_target_parent(workspace: Path) -> None:
    (workspace / "a.py").write_text("x", encoding="utf-8")

    error = _prepare(workspace, "*** MOVE a.py -> nowhere/b.py")

    assert isinstance(error, PreparationError)
    assert "父目录不存在" in error.message


def test_moving_a_symlink_is_refused(workspace: Path) -> None:
    (workspace / "real.py").write_text("x", encoding="utf-8")
    (workspace / "link.py").symlink_to(workspace / "real.py")

    error = _prepare(workspace, "*** MOVE link.py -> moved.py")

    assert isinstance(error, PreparationError)
    assert "符号链接" in error.message


# ---- 执行前复核 ----


def test_a_file_changed_after_prepare_is_not_written(workspace: Path) -> None:
    """expected_state 守卫: 计划生成后文件变了就拒绝, 不按旧内容硬写回去."""
    target = workspace / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    plan = _prepare(workspace, "*** UPDATE a.py\n*** FIND\nx = 1\n*** REPLACE\nx = 2")
    assert isinstance(plan, ToolPlan)

    target.write_text("someone else wrote this\n", encoding="utf-8")
    result = _tool().perform(plan, _context(workspace))

    assert result.status is ToolResultStatus.TOOL_ERROR
    assert target.read_text(encoding="utf-8") == "someone else wrote this\n"


def test_one_envelope_is_one_plan_with_every_target(workspace: Path) -> None:
    """本 ADR 的核心收益: 一次逻辑改动 = 一次审批 = 一个恢复点.

    合并前, 下面这四件事是四次独立审批, 用户逐条点头却看不到整体.
    """
    (workspace / "a.py").write_text("x = 1\n", encoding="utf-8")
    (workspace / "gone.py").write_text("bye", encoding="utf-8")
    (workspace / "m.py").write_text("move", encoding="utf-8")

    plan = _prepare(
        workspace,
        "*** UPDATE a.py\n*** FIND\nx = 1\n*** REPLACE\nx = 2\n"
        "*** NEW b.py\nnew\n"
        "*** DELETE gone.py\n"
        "*** MOVE m.py -> moved.py",
    )

    assert isinstance(plan, ToolPlan)
    assert len(_operations(plan)) == 4
    assert len(plan.effects.write_paths) == 2
    assert len(plan.effects.delete_paths) == 1
    assert len(plan.effects.move_pairs) == 1
    assert plan.capabilities == frozenset(
        {
            Capability.WORKSPACE_WRITE,
            Capability.WORKSPACE_DELETE,
            Capability.PATH_MOVE,
        }
    )


def test_the_whole_envelope_applies(workspace: Path) -> None:
    (workspace / "a.py").write_text("x = 1\n", encoding="utf-8")
    (workspace / "gone.py").write_text("bye", encoding="utf-8")
    (workspace / "m.py").write_text("move", encoding="utf-8")
    plan = _prepare(
        workspace,
        "*** UPDATE a.py\n*** FIND\nx = 1\n*** REPLACE\nx = 2\n"
        "*** NEW pkg/b.py\nnew\n"
        "*** DELETE gone.py\n"
        "*** MOVE m.py -> moved.py",
    )
    assert isinstance(plan, ToolPlan)

    result = _tool().perform(plan, _context(workspace))

    assert result.status is ToolResultStatus.OK
    assert (workspace / "a.py").read_text(encoding="utf-8") == "x = 2\n"
    assert (workspace / "pkg" / "b.py").read_text(encoding="utf-8") == "new\n"
    assert not (workspace / "gone.py").exists()
    assert (workspace / "moved.py").read_text(encoding="utf-8") == "move"


def test_the_envelope_refuses_symlink_targets(workspace: Path) -> None:
    (workspace / "real.py").write_text("x", encoding="utf-8")
    (workspace / "link.py").symlink_to(workspace / "real.py")

    error = _prepare(workspace, "*** UPDATE link.py\n*** FIND\nx\n*** REPLACE\ny")

    assert isinstance(error, PreparationError)
    assert "符号链接" in error.message


def test_an_empty_patch_is_rejected(workspace: Path) -> None:
    error = _prepare(workspace, "   ")

    assert isinstance(error, PreparationError)
    assert error.field_path == "patch"


# ---- 改过谁: 上下文管理据此把改动前那次读标记成过时的 (ADR-0032 决策 4) ----


def _perform(workspace: Path, patch: str):  # type: ignore[no-untyped-def]
    plan = _prepare(workspace, patch)
    assert isinstance(plan, ToolPlan)
    return _tool().perform(plan, _context(workspace))


def test_a_write_reports_every_path_it_touched(workspace: Path) -> None:
    (workspace / "a.py").write_text("old\n", encoding="utf-8")
    (workspace / "gone.py").write_text("x", encoding="utf-8")

    result = _perform(
        workspace,
        "*** UPDATE a.py\n*** FIND\nold\n*** REPLACE\nnew\n\n"
        "*** NEW b.py\nfresh\n\n"
        "*** DELETE gone.py",
    )

    assert result.provenance is not None
    assert set(result.provenance.mutated_paths) == {
        str(workspace / "a.py"),
        str(workspace / "b.py"),
        str(workspace / "gone.py"),
    }


def test_a_move_reports_both_ends(workspace: Path) -> None:
    (workspace / "from.py").write_text("x", encoding="utf-8")

    result = _perform(workspace, "*** MOVE from.py -> to.py")

    assert result.provenance is not None
    assert set(result.provenance.mutated_paths) == {
        str(workspace / "from.py"),
        str(workspace / "to.py"),
    }


def test_the_reported_path_matches_what_fs_read_records(workspace: Path) -> None:
    """两个工具必须给出同一种路径写法, 否则这条通知一次也不会命中.

    去重按路径字符串归组: fs_read 记 realpath, 补丁这边记别的形式的话, 两条记录在
    上下文管理眼里就是两个不相干的文件 —— 不报错, 只是通知永远不发.
    """
    target = workspace / "a.py"
    target.write_text("old\n", encoding="utf-8")
    read = ReadFileTool(ResourceGovernor(), None)
    read_plan = read.prepare(
        ToolInvocationRequest(
            invocation_id="inv-read",
            tool_name="fs_read",
            arguments={"path": str(target)},
            tool_call_id="c0",
        ),
        _context(workspace),
    )
    assert isinstance(read_plan, ToolPlan)
    read_result = read.perform(read_plan, _context(workspace))

    written = _perform(workspace, "*** UPDATE a.py\n*** FIND\nold\n*** REPLACE\nnew")

    assert read_result.provenance is not None
    assert written.provenance is not None
    assert read_result.provenance.source_path in written.provenance.mutated_paths


def test_a_partial_apply_still_reports_what_landed(workspace: Path) -> None:
    """中途停下同样改过东西.

    不报的话, 已经写下去的那几个文件在上下文里仍然是改动前的样子 —— 而这正是最需要
    这条通知的时刻.
    """
    (workspace / "a.py").write_text("old\n", encoding="utf-8")
    (workspace / "later.py").write_text("x", encoding="utf-8")
    plan = _prepare(
        workspace,
        "*** UPDATE a.py\n*** FIND\nold\n*** REPLACE\nnew\n\n*** DELETE later.py",
    )
    assert isinstance(plan, ToolPlan)
    tool = ApplyPatchTool(
        lambda path, content: Path(path).write_text(content, encoding="utf-8"),
        lambda path, content: Path(path).write_text(content, encoding="utf-8"),
        _explode,
        lambda source, target: Path(source).rename(target),
        lambda path: Path(path).mkdir(parents=True, exist_ok=True),
    )

    result = tool.perform(plan, _context(workspace))

    assert result.status is ToolResultStatus.TOOL_ERROR
    assert result.provenance is not None
    assert result.provenance.mutated_paths == (str(workspace / "a.py"),)


def _explode(path: str) -> None:
    raise OSError("盘满了")


def test_a_failed_find_quotes_the_nearest_block(tmp_path: Path) -> None:
    """定位失败时给出证据, 而不是"先读回来再试".

    原来的兜底是 " 先用 fs_read 读回当前内容, 再照抄其中一段". 那句话是对的但没有信息
    —— 模型手里那份 FIND 与文件哪里不一样它依然看不见, 于是最省力的下一步是把整个文件
    读回来再猜一次. 真实会话里 110 次 fs_apply_patch 有 17 次 apply_failed, 而且是成串
    出现的: 定位失败 -> 重读 -> 再失败.
    """
    from forgecli.application.tools.builtin.patch_apply import _miss_hint

    source = (
        "def authenticate(user, password):\n"
        "    if not user:\n"
        '        raise ValueError("no user")\n'
        "    return check(user, password)\n"
    )
    # 模型凭记忆复述的版本: 参数名与消息都差一点.
    old = (
        "def authenticate(username, password):\n"
        "    if not username:\n"
        '        raise ValueError("missing user")\n'
        "    return check(username, password)\n"
    )

    hint = _miss_hint(source, old)

    assert "最接近的一段在第 1 行" in hint
    assert "相似度" in hint
    assert "raise ValueError" in hint


def test_an_unrelated_file_gets_no_quote(tmp_path: Path) -> None:
    """引一段其实不相干的代码, 比说"找不到"更糟: 模型会照着它改, 然后在另一个位置
    再失败一次.
    """
    from forgecli.application.tools.builtin.patch_apply import _miss_hint

    hint = _miss_hint("import os\nimport sys\n", "class TotallyDifferentThing:\n")

    assert "最接近的一段" not in hint
