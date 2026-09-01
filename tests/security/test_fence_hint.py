"""围栏拒绝的识别与提示 (ADR-0030).

要钉住的是**方向**: 这条启发式只产出一段说明, 从不放行任何东西. 认错了多一句没用的
提示, 认漏了回到今天的样子 —— 两个方向都不通往越界.

它存在的理由是可量化的: 日志里一次 `mvn` 被拦之后, 模型连发五条命令满机器找 maven,
五次模型调用合计约二十万输入 token. 它读不出"命令不存在"与"命令被拿掉了"的区别, 而这
两者该走完全不同的下一步.
"""

from __future__ import annotations

from forgecli.application.tool_request.fence_hint import fence_hint
from forgecli.domain.execution.denial import blocked_paths
from forgecli.domain.execution.fence import FencePolicy

_FENCE = FencePolicy(
    writable_roots=("/ws",),
    denied_read_paths=("/home/u/.ssh",),
    instance_temp_root="/tmp/forge-1",
)


# ---- 识别: 从各家工具的措辞里认出被拒的路径 ----


def test_common_denial_shapes_yield_their_path() -> None:
    """每个工具的措辞都不一样, 共用的只有底层 errno 那句话."""
    assert blocked_paths("mkdir: /home/u/.m2/repo: Permission denied") == (
        "/home/u/.m2/repo",
    )
    assert blocked_paths("sh: /opt/out.log: Read-only file system") == ("/opt/out.log",)
    assert blocked_paths(
        "npm ERR! Error: EACCES: permission denied, mkdir '/home/u/.npm/_cacache'"
    ) == ("/home/u/.npm/_cacache",)


def test_a_quoted_path_with_spaces_survives() -> None:
    """按空白切词会把带空格的路径切碎, 而那正是 macOS 上最常见的形状."""
    assert blocked_paths(
        "touch: cannot touch '/home/u/My Files/x': Permission denied"
    ) == ("/home/u/My Files/x",)


def test_a_failure_that_is_not_a_denial_yields_nothing() -> None:
    """方向: 认不出就什么都不说, 而不是猜一个路径出来."""
    assert blocked_paths("grep: foo.txt: No such file or directory") == ()
    assert blocked_paths("make: *** [build] Error 2") == ()


def test_a_localised_denial_is_still_recognised() -> None:
    """环境继承启动 shell 之后 LANG 是用户的, coreutils 会说中文."""
    assert blocked_paths("mkdir: 无法创建目录 '/home/u/.npm': 权限不够") == (
        "/home/u/.npm",
    )


# ---- 提示: 只在真的可能是围栏时才出 ----


def test_no_hint_without_a_real_fence() -> None:
    """围栏没立起来时那句 Permission denied 只能是文件系统本身的权限.

    这一条是方向性的: 说成围栏就是在骗模型, 而模型会据此去请用户授权一个根本不需要
    授权的目录.
    """
    output = "mkdir: /home/u/.m2/repo: Permission denied"

    assert fence_hint(output, _FENCE, confined=False) == ""


def test_a_path_the_fence_already_allows_is_not_blamed_on_the_fence() -> None:
    """工作区内被拒 = 真的权限不够, 与围栏无关."""
    output = "mkdir: /ws/build/out: Permission denied"

    assert fence_hint(output, _FENCE, confined=True) == ""


def test_a_protected_path_is_not_proposed() -> None:
    """受保护路径授权不开, 提议它只会让用户点一个必然失败的按钮."""
    output = "cat: /home/u/.ssh/id_rsa: Permission denied"

    assert fence_hint(output, _FENCE, confined=True) == ""


def test_a_blocked_path_outside_the_fence_produces_an_actionable_hint() -> None:
    """三件事都要说到, 少一件模型就会走错路."""
    output = "mkdir: /home/u/.m2/repository: Permission denied"

    hint = fence_hint(output, _FENCE, confined=True)

    assert "/home/u/.m2/repository" in hint
    # 1. 不是路径写错了 —— 否则它会去改一个本来就对的路径.
    assert "不是路径写错了" in hint
    # 2. 放开边界要用户做, 模型自己做不到.
    assert "用户" in hint
    # 3. 还有一条不需要放开边界的路.
    assert "工作区内" in hint
    # 不叫它重试: 边界没变之前重跑必然再失败一次, 而那是一整轮模型调用.
    assert "重试" not in hint


# ---- 回填: 提示要同时到达本轮正文与跨回合的那一行 ----


def _observation(hint: str):
    from forgecli.application.tool_request.observations import (
        ObservationKind,
        ToolObservation,
    )
    from forgecli.domain.tool.result import ContentPart, ToolResult, ToolResultStatus

    result = ToolResult(
        invocation_id="inv",
        tool_name="shell_run",
        status=ToolResultStatus.OK,
        content_parts=(ContentPart(text="mkdir: /home/u/.m2: Permission denied"),),
    )
    return ToolObservation(
        kind=ObservationKind.TOOL_RESULT,
        message="ok",
        invocation_id="inv",
        tool_name="shell_run",
        result=result,
        fence_hint=hint,
    )


def test_the_hint_is_appended_to_the_output_not_replacing_it() -> None:
    """命令自己的输出仍然是模型判断成败的依据; 提示只补上它读不出来的那一半."""
    body = _observation("[被围栏拦下] 说明").render()

    assert "Permission denied" in body
    assert "[被围栏拦下] 说明" in body


def test_the_cross_turn_line_says_it_was_the_fence() -> None:
    """跨回合只留一行 (ADR-0032 决策 7), 而"上次是被围栏拦的"正是下一步的依据."""
    assert "(被围栏拦下)" in _observation("说明").digest_line()
    assert "(被围栏拦下)" not in _observation("").digest_line()
