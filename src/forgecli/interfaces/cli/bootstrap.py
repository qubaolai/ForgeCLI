"""组合根：装配整个交互式会话的依赖并返回可运行的 Repl。"""

from __future__ import annotations

from rich.console import Console

from forgecli.application.intent_router import IntentRouter
from forgecli.application.session.state import SessionState
from forgecli.interfaces.cli.menu_presenter import RichMenuPresenter
from forgecli.interfaces.cli.output import RichOutput
from forgecli.interfaces.cli.repl import Repl
from forgecli.interfaces.cli.wiring import build_registry


def build_repl() -> Repl:
    console = Console()
    state = SessionState()
    output = RichOutput(console=console)
    presenter = RichMenuPresenter(console=console)
    registry = build_registry(state=state, presenter=presenter, output=output)
    router = IntentRouter(registry=registry)
    return Repl(
        console=console,
        router=router,
        registry=registry,
        state=state,
        output=output,
    )
