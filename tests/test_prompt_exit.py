"""ForgePrompt 的双击 Ctrl-C 退出：500ms 内有效，超时复位（仿 Claude Code）。

直接驱动 _arm_exit / _disarm_exit / _reset_exit（在运行的事件循环内），
不拉起整个 prompt_toolkit 全屏应用。
"""

from __future__ import annotations

import asyncio

from forgecli.interfaces.cli.prompt_loop import ForgePrompt

_COMMANDS = [("help", "查看帮助"), ("exit", "退出会话")]


class _FakeApp:
    def __init__(self) -> None:
        self.invalidated = 0

    def invalidate(self) -> None:
        self.invalidated += 1


def test_arm_then_timeout_resets_and_redraws() -> None:
    prompt = ForgePrompt(_COMMANDS)
    app = _FakeApp()

    async def scenario() -> tuple[bool, bool, int]:
        prompt._arm_exit(app)  # type: ignore[arg-type]
        armed_immediately = prompt._exit_armed
        await asyncio.sleep(_window() + 0.2)  # 超过窗口，定时器应触发复位
        return armed_immediately, prompt._exit_armed, app.invalidated

    armed, armed_after, invalidated = asyncio.run(scenario())
    assert armed is True  # 第一次按下后进入待退出
    assert armed_after is False  # 超时后自动复位
    assert invalidated == 1  # 复位时重绘了一次（提示行还原）


def test_disarm_cancels_timer() -> None:
    prompt = ForgePrompt(_COMMANDS)
    app = _FakeApp()

    async def scenario() -> tuple[bool, int]:
        prompt._arm_exit(app)  # type: ignore[arg-type]
        prompt._disarm_exit()  # 窗口内被处理（第二次退出 / 打字）→ 撤掉定时器
        await asyncio.sleep(_window() + 0.2)
        return prompt._exit_armed, app.invalidated

    armed, invalidated = asyncio.run(scenario())
    assert armed is False
    assert invalidated == 0  # 定时器已取消，没有迟到的复位重绘


def test_second_arm_resets_the_timer() -> None:
    prompt = ForgePrompt(_COMMANDS)
    app = _FakeApp()

    async def scenario() -> int:
        prompt._arm_exit(app)  # type: ignore[arg-type]
        first = prompt._reset_handle
        await asyncio.sleep(_window() / 2)
        prompt._arm_exit(app)  # type: ignore[arg-type]  # 再次按下应换一个新定时器
        assert prompt._reset_handle is not first
        await asyncio.sleep(_window() + 0.2)
        return app.invalidated

    # 旧定时器被取消、只有新定时器触发一次复位。
    assert asyncio.run(scenario()) == 1


def _window() -> float:
    from forgecli.interfaces.cli import prompt_loop

    return prompt_loop._EXIT_WINDOW
