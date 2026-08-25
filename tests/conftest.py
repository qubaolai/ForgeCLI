"""全局测试隔离与共享的日志捕获.

`forgecli.shared.observability.context.update` 是有意不带还原点的 (见该函数注释):
生产路径靠外层 `bind` 收口, 而用例经常直接驱动 BuiltinAgentLoop, 于是它写进去的
session / turn / step 会一路留到下一个用例的日志行上. 断言别人的日志内容时, 那些
残留会以"另一个用例失败"的形式表现出来.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest

from forgecli.shared.observability.configure import reset_logging
from forgecli.shared.observability.context import reset as reset_run_context
from forgecli.shared.observability.log import ROOT_LOGGER_NAME


@pytest.fixture(autouse=True)
def _clean_run_context() -> Iterator[None]:
    reset_run_context()
    yield
    reset_run_context()
    # 留下的 FileHandler 会往已被删掉的 tmp_path 继续写.
    reset_logging()


@pytest.fixture
def records(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """让 caplog 看得见 forgecli 的日志.

    生产装配把 `forgecli` logger 的 propagate 关掉了 (记录不再冒到 logging 根, 免得
    被别人装的 handler 再打一遍), 而 caplog 恰恰是把 handler 装在根上的 —— 所以这里
    显式放开 propagate, 并在用例结束后还原.
    """
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    previous = (logger.propagate, logger.level)
    logger.propagate = True
    caplog.set_level(logging.DEBUG, logger=ROOT_LOGGER_NAME)
    yield caplog
    logger.propagate, logger.level = previous
