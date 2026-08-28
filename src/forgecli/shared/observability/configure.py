"""日志装配: 决定写到哪, 写多细, 以及谁来收未捕获的异常.

只在两个进程入口各调一次 (`interfaces/cli/bootstrap.run`, `interfaces/web/server.run`).
业务代码只 `get_log(__name__)` 然后写, 永远不碰 handler —— 一个模块自己加 handler,
另一个模块的日志就会莫名其妙地写两份.

三条取舍:

1. **默认只写文件, 不写终端.** CLI 的 REPL 与 Rich Live 区独占终端 (ADR-0016 §8.1),
   往 stderr 打日志会把正在渲染的活动区戳花. 要跟着看就开 `FORGE_LOG_CONSOLE=1`,
   或者对着文件 `tail -f`.
2. **一次运行一个文件**, 而不是共用一个按大小切分的文件. `~/.forge` 是跨项目共享的,
   `forge.lock` 只锁到项目一级 —— 两个项目同时跑时, 共用文件的切分会互相截断.
   文件名带 pid, 谁写的一目了然; 旧文件按数量清理.
3. **未捕获异常也写进来.** 主线程崩在终端上还看得见, 后台 turn 线程崩掉时终端上
   什么都没有 —— 那正是最需要日志的一种 bug.

这些开关全部来自**配置文件** (`logging.*`, 见 domain/config/config_keys.py), 由
`interfaces/runtime/logging_wiring` 读出来传进这个函数. 这里不读环境变量, 也不读文件:
一个住在 shared 的模块认识 Forge 主目录在哪, 就等于把 infrastructure 的知识搬进了最底层.

原先它们是五个 FORGE_LOG_* 环境变量. 环境变量改起来看着方便, 但它们**在设置面板里看不见,
也改不了** —— 用户要打开一次 debug 日志, 得先知道有这么个变量名, 再在启动命令前面拼上去,
而且下次启动就没了. 配置项在 /config 与 Web 设置页里各是一行, 改完写进 config.json.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType

from forgecli.shared.observability.log import (
    ROOT_LOGGER_NAME,
    get_log,
    reset_max_value_chars,
    set_max_value_chars,
)
from forgecli.shared.observability.log import (
    max_value_chars as current_max_value_chars,
)

__all__ = [
    "LoggingStatus",
    "configure_logging",
    "logging_status",
    "reset_logging",
]

_LEVELS: dict[str, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

# 收进同一个文件的第三方 logger. 排查"卡在 provider 上"时, httpx 的连接与重试
# 记录跟 Forge 自己的 gateway 记录必须在同一条时间线上才对得起来.
_THIRD_PARTY = ("httpx", "httpcore", "uvicorn.error", "uvicorn.access", "asyncio")

# 保留多少个历史日志文件. 一次运行一个文件, 20 个大约够回看最近几天的问题.
_KEEP_FILES = 20

_LINE = (
    "%(asctime)s.%(msecs)03d %(levelname)-5s %(module_path)s "
    "[%(threadName)s] %(message)s"
)
_TIME = "%Y-%m-%dT%H:%M:%S"


class _ForgeFormatter(logging.Formatter):
    """把 logger 名里的 `forgecli.` 前缀去掉再输出.

    每一行都以 `forgecli.` 开头等于没有信息 —— 这个文件里不会有别的包. 后面的层名
    (`application.tool_request.coordinator`) 一个字不改: 日志里的模块路径要能直接
    拿去 open, 缩写成 `app.` 就得先在脑子里翻译一次.
    """

    def format(self, record: logging.LogRecord) -> str:
        name = record.name
        prefix = f"{ROOT_LOGGER_NAME}."
        record.module_path = name[len(prefix) :] if name.startswith(prefix) else name
        return super().format(record)


_log = get_log(__name__)

# 本模块装上去的 handler, 连同它挂在哪个 logger 上. 重复装配时照着这份清单精确拆,
# 而不是"把所有 handler 都删了" —— 后者会连别的库自己装的 handler 一起拆掉.
_attached: list[tuple[logging.Logger, logging.Handler]] = []


@dataclass(frozen=True)
class LoggingStatus:
    """当前生效的日志装配. 供 `/diagnostics` 直接展示 —— 排查的第一个问题永远是
    "日志到底写在哪, 开到多细"."""

    level: str = "off"
    file: Path | None = None
    console: bool = False
    max_value_chars: int = 0
    configured: bool = False


_status = LoggingStatus()


def logging_status() -> LoggingStatus:
    """当前日志装配 (未装配时 configured=False)."""
    return _status


def configure_logging(
    *,
    directory: Path,
    level: str = "",
    console: bool = False,
    max_value_chars: str = "",
    include_http: bool = False,
) -> LoggingStatus:
    """装上文件 (可选终端) handler 并接管未捕获异常; 重复调用会先拆掉旧的.

    参数一一对应 `logging.*` 配置项. `directory` 已经是解析好的绝对路径 ——
    "配置留空时用哪个目录"是调用方的事, 这个模块不认识 Forge 主目录.

    `max_value_chars` 是字符串而不是 int: 配置项留空表示"用内置上限", 而 0 表示
    "不截断". 用 `int | None` 也能表达, 但那要求每个调用方先做一次同样的转换.
    """
    global _status

    resolved_level = _LEVELS.get(level.strip().lower(), logging.INFO)
    root = logging.getLogger(ROOT_LOGGER_NAME)
    _detach(root)
    root.setLevel(resolved_level)
    # 不再向 logging 根 logger 冒泡: 冒上去会被 basicConfig 装的 handler 或者别的库
    # 装的 handler 再打一遍, 同一条记录出现两次.
    root.propagate = False

    set_max_value_chars(_resolve_max_value_chars(max_value_chars))
    formatter = _ForgeFormatter(_LINE, datefmt=_TIME)

    path = _open_file(root, directory, formatter)
    if console:
        stream: logging.Handler = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        root.addHandler(stream)
        _attached.append((root, stream))

    if include_http:
        _attach_third_party(root, resolved_level)

    _install_exception_hooks()

    _status = LoggingStatus(
        level=logging.getLevelName(resolved_level).lower(),
        file=path,
        console=console,
        max_value_chars=current_max_value_chars(),
        configured=True,
    )
    _log.info(
        "logging.configured",
        level=_status.level,
        file=str(path) if path else None,
        console=console,
        max_value_chars=_status.max_value_chars,
        pid=os.getpid(),
    )
    return _status


# ---- 各段 ----


def _resolve_max_value_chars(configured: str) -> int:
    """留空 = 用内置上限; 写了数字就用它 (0 表示不截断).

    读不懂的值退回内置上限而不是报错: 这一步发生在日志装配之前, 报错的话连"配置写错了"
    这条记录都写不出来.
    """
    raw = configured.strip()
    if not raw:
        return current_max_value_chars()
    try:
        return int(raw)
    except ValueError:
        return current_max_value_chars()


def _detach(_root: logging.Logger) -> None:
    """拆掉上一次装的 handler, 让重复装配不叠加."""
    for logger, handler in _attached:
        logger.removeHandler(handler)
    for _, handler in _attached:
        handler.close()
    _attached.clear()


def _open_file(
    root: logging.Logger, directory: Path, formatter: logging.Formatter
) -> Path | None:
    """建目录, 开文件, 清旧文件. 任一步失败就退回"只有终端", 绝不因为写不了日志
    而让 forge 起不来."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"forge-{stamp}-{os.getpid()}.log"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
    except OSError as exc:
        print(f"forge: 无法写日志文件 {path}: {exc}", file=sys.stderr)
        return None
    handler.setFormatter(formatter)
    root.addHandler(handler)
    _attached.append((root, handler))
    _prune(directory, keep=path)
    _point_at_latest(directory, path)
    return path


def _prune(directory: Path, *, keep: Path) -> None:
    try:
        files = sorted(
            (
                item
                for item in directory.glob("forge-*.log")
                if item != keep and item.name != "forge-latest.log"
            ),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for stale in files[_KEEP_FILES - 1 :]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass  # 清不掉旧文件不影响这一次运行


def _point_at_latest(directory: Path, path: Path) -> None:
    """`forge-latest.log` 指向本次运行, 让 `tail -f` 有个固定路径可以盯."""
    link = directory / "forge-latest.log"
    try:
        link.unlink(missing_ok=True)
        link.symlink_to(path.name)
    except (OSError, NotImplementedError):
        pass  # Windows 上没有权限建符号链接是常态, 不值得报错


def _attach_third_party(root: logging.Logger, level: int) -> None:
    owned = [handler for logger, handler in _attached if logger is root]
    for name in _THIRD_PARTY:
        third = logging.getLogger(name)
        third.setLevel(level)
        for handler in owned:
            third.addHandler(handler)
            _attached.append((third, handler))


def _install_exception_hooks() -> None:
    """未捕获异常进日志. 链到原 hook 上: 终端该打的 traceback 照打."""
    previous = sys.excepthook

    def hook(
        kind: type[BaseException],
        value: BaseException,
        traceback: TracebackType | None,
    ) -> None:
        if not issubclass(kind, KeyboardInterrupt):
            _log.exception(
                "process.uncaught",
                exc_info=value,
                error=kind.__name__,
                message=str(value),
            )
        previous(kind, value, traceback)

    sys.excepthook = hook

    previous_thread_hook = threading.excepthook

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        if not issubclass(args.exc_type, SystemExit) and args.exc_value is not None:
            _log.exception(
                "thread.uncaught",
                exc_info=args.exc_value,
                thread=args.thread.name if args.thread else "?",
                error=args.exc_type.__name__,
                message=str(args.exc_value),
            )
        previous_thread_hook(args)

    threading.excepthook = thread_hook


def reset_logging() -> None:
    """拆掉本模块装过的一切, 把 forgecli logger 还原成未装配的样子.

    生产路径不调它 —— 一个进程装配一次就够了. 它存在是因为测试里"上一个用例装的
    handler 还在往一个已经删掉的临时目录写"这件事会以最难看的方式表现出来: 断言在
    另一个用例里失败, 而那个用例根本没碰过日志.
    """
    global _status

    root = logging.getLogger(ROOT_LOGGER_NAME)
    _detach(root)
    root.setLevel(logging.NOTSET)
    root.propagate = True
    reset_max_value_chars()
    _status = LoggingStatus()
