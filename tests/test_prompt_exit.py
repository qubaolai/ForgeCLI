"""ForgePrompt 的双击 Ctrl-C 退出：窗口内有效，超时复位（仿 Claude Code）。

直接驱动 _arm_exit / _disarm_exit / _reset_exit（在运行的事件循环内），
不拉起整个 prompt_toolkit 全屏应用。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from prompt_toolkit.keys import Keys

from forgecli.interfaces.cli.prompt_loop import ForgePrompt, QuitSignal

_COMMANDS = [("help", "查看帮助"), ("status", "查看状态")]


class _FakeApp:
    def __init__(self) -> None:
        self.invalidated = 0
        self.exception: BaseException | None = None

    def invalidate(self) -> None:
        self.invalidated += 1

    def exit(
        self,
        *,
        exception: BaseException | None = None,
        result: str | None = None,
    ) -> None:
        self.exception = exception


class _FakeBuffer:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.complete_state = None
        self.deleted = 0
        self.cancelled = 0
        self.reset_count = 0

    def delete(self) -> None:
        self.deleted += 1

    def cancel_completion(self) -> None:
        self.cancelled += 1

    def reset(self) -> None:
        self.reset_count += 1
        self.text = ""


class _FakeEvent:
    def __init__(self, text: str = "") -> None:
        self.current_buffer = _FakeBuffer(text)
        self.app = _FakeApp()


def _handler(prompt: ForgePrompt, key: Keys) -> Callable[[object], object]:
    for binding in prompt._key_bindings().bindings:
        if binding.keys == (key,):
            return binding.handler
    raise AssertionError(f"missing key binding: {key}")


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


def test_empty_ctrl_d_does_not_exit() -> None:
    prompt = ForgePrompt(_COMMANDS)
    event = _FakeEvent("")

    _handler(prompt, Keys.ControlD)(event)

    assert event.app.exception is None
    assert event.current_buffer.deleted == 0


def test_ctrl_d_with_content_deletes_without_exit() -> None:
    prompt = ForgePrompt(_COMMANDS)
    event = _FakeEvent("hello")

    _handler(prompt, Keys.ControlD)(event)

    assert event.app.exception is None
    assert event.current_buffer.deleted == 1


def test_second_empty_ctrl_c_exits_with_quit_signal() -> None:
    prompt = ForgePrompt(_COMMANDS)
    event = _FakeEvent("")
    interrupt = _handler(prompt, Keys.ControlC)

    async def scenario() -> BaseException | None:
        interrupt(event)
        assert prompt._exit_armed is True
        interrupt(event)
        return event.app.exception

    assert isinstance(asyncio.run(scenario()), QuitSignal)


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
