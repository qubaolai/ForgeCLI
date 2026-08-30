"""结构化日志门面: 一行一条记录, `事件名 键=值 键=值`.

为什么不直接用 `logging.getLogger(__name__).info("...")`:

- **格式统一才查得动.** 自由格式的日志半年后就是一堆各写各的句子, `grep exit_code=`
  查不到任何东西. 固定成 `键=值` 之后, 从"哪次 shell_run 非零退出"到"哪个 provider
  在超时", 都是一条 grep.
- **上下文自动带上.** 每一行自动拼进当前 `RunContext` 的 session / turn / step /
  req / inv / tool. 调用点不为了写日志在方法签名上多带参数.
- **贵的字符串不白拼.** 详细日志会把整段工具输出与模型回复写进去; 级别没开到 DEBUG
  时 `isEnabledFor` 直接返回, 一个字符串都不构造.

**不做脱敏** (ADR-0035 决策 4): 工具入参, 命令 argv, 模型请求与回复原文都照写.
日志落在本机 `~/.forge/logs/` 下, 与会话事件, 恢复快照同一层信任假设. 唯一的例外是
API key 的**值**: 写的是它的来源与指纹 (`env:OPENAI_API_KEY`, `sk-a1..7f9c`), 因为
排查从来不需要那串字符本身, 而日志文件是最容易被贴进 issue 的东西.

值渲染只有一条规则: **一条记录一行**. 多行内容 (文件内容, stderr, 模型回复) 走
`json.dumps` 转义, 换行变成 `\\n` 留在同一行里 —— 否则 `grep` 出来的永远是半截.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field

from forgecli.shared.observability.context import current
from forgecli.shared.observability.metrics import METRICS

__all__ = [
    "Log",
    "Span",
    "get_log",
    "max_value_chars",
    "reset_max_value_chars",
    "set_max_value_chars",
]

ROOT_LOGGER_NAME = "forgecli"

# 单个值渲染后的字符上限. 0 表示不截断.
#
# 4000 是"够看清一次调用"与"一行不至于把编辑器拖死"之间的取舍: 一次 fs_read
# 可能回来 500KB, 全写进日志之后再想翻这个文件就得靠 sed. 需要全文时用
# FORGE_LOG_MAX_VALUE=0 关掉截断, 它是给"这一次一定要看到全部"准备的.
_DEFAULT_MAX_VALUE_CHARS = 4000
_max_value_chars = _DEFAULT_MAX_VALUE_CHARS

# 不带引号也不会被读错的短值: 没有空白, 没有等号与引号, 且不长.
_UNQUOTED_MAX = 64
_NEEDS_QUOTING = set(" \t\r\n\"'=")

# 库的惯例: 没人配置过 handler 时安静地丢弃, 而不是让 logging 的 lastResort 把
# WARNING 打到 stderr —— 那会在测试输出里冒出来.
logging.getLogger(ROOT_LOGGER_NAME).addHandler(logging.NullHandler())


def max_value_chars() -> int:
    """当前的单值截断上限 (0 表示不截断)."""
    return _max_value_chars


def set_max_value_chars(limit: int) -> None:
    """设置单值截断上限. 由 `configure_logging` 依据环境变量调用."""
    global _max_value_chars
    _max_value_chars = max(0, limit)


def reset_max_value_chars() -> None:
    """回到默认上限.

    与 `reset_logging` 配套: 一个用例把它调成 0 之后, 后面所有用例都不再截断.
    """
    set_max_value_chars(_DEFAULT_MAX_VALUE_CHARS)


def _truncate(text: str) -> str:
    limit = _max_value_chars
    if limit <= 0 or len(text) <= limit:
        return text
    return f"{text[:limit]}...(+{len(text) - limit} chars)"


def _render_str(value: str) -> str:
    if (
        len(value) <= _UNQUOTED_MAX
        and value
        and not _NEEDS_QUOTING.intersection(value)
        and value.isprintable()
    ):
        return _truncate(value)
    return _truncate(json.dumps(value, ensure_ascii=False))


def _render(value: object) -> str:
    """把任意值渲染成不含换行的一段文本."""
    if value is None or isinstance(value, bool | int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.3f}"
    if isinstance(value, str):
        return _render_str(value)
    if isinstance(value, Mapping | Sequence | set | frozenset):
        try:
            dumped = json.dumps(
                sorted(value) if isinstance(value, set | frozenset) else value,
                ensure_ascii=False,
                default=str,
            )
        except (TypeError, ValueError):
            dumped = str(value)
        return _truncate(dumped)
    return _render_str(str(value))


def _compose(event: str, fields: Mapping[str, object]) -> str:
    """`event ctx=... 调用字段=...`. 上下文在前: 同一轮的行看起来对得齐.

    同名时**调用点的值赢**, 且只出现一次. 这不是细节: 工具链路会把 tool 同时放进
    运行上下文和那一行的字段里, 两边各打一遍就成了 `tool=fs_find tool=fs_find`,
    而按 `tool=` 切分的解析会读到一个它没预料到的重复键.
    """
    merged: dict[str, object] = dict(current().fields())
    merged.update(fields)
    parts = [event]
    for name, value in merged.items():
        parts.append(f"{name}={_render(value)}")
    return " ".join(parts)


@dataclass
class Span:
    """一段计时中的字段袋: 执行过程里才知道的事实往这里放, 收尾时一起写出去."""

    fields: dict[str, object] = field(default_factory=dict)

    def set(self, **fields: object) -> None:
        """补充收尾行要带的字段 (退出码, 命中条数, 是否走了缓存)."""
        self.fields.update(fields)


class Log:
    """一个模块的日志入口. 用 `get_log(__name__)` 取, 不要直接构造."""

    __slots__ = ("_logger",)

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def debug(self, event: str, **fields: object) -> None:
        """逐步细节: 每次模型调用的完整请求, 每次工具调用的完整入参."""
        self._write(logging.DEBUG, event, fields)

    def info(self, event: str, **fields: object) -> None:
        """一次真实运行的骨架: 启动, turn 起止, 模型调用, 工具裁决与执行结果."""
        self._write(logging.INFO, event, fields)

    def warning(self, event: str, **fields: object) -> None:
        """能继续跑, 但结果可能不是用户预期的 (重试, 降级, 截断, 预算闸)."""
        self._write(logging.WARNING, event, fields)

    def error(self, event: str, **fields: object) -> None:
        """这一步失败了."""
        self._write(logging.ERROR, event, fields)

    def exception(
        self,
        event: str,
        *,
        exc_info: bool | BaseException = True,
        **fields: object,
    ) -> None:
        """在 except 块里写: 附带完整 traceback.

        `exc_info` 默认取当前正在处理的异常; 在 `sys.excepthook` 这类"异常已经不在
        处理中"的位置上, 把异常对象显式传进来.
        """
        if self._logger.isEnabledFor(logging.ERROR):
            self._logger.error(_compose(event, fields), exc_info=exc_info)

    def enabled_for_debug(self) -> bool:
        """DEBUG 是否会被写出去. 给"构造这个字段本身就很贵"的调用点用."""
        return self._logger.isEnabledFor(logging.DEBUG)

    @contextmanager
    def span(self, event: str, **fields: object) -> Iterator[Span]:
        """给一段执行计时, 并把耗时同时记进 `METRICS`.

        产出三种行: `event.start` (DEBUG), `event.ok` / `event.error` (INFO / ERROR),
        收尾行带 `elapsed_ms`. 异常照常上抛 —— 这里只观察, 不改变控制流.
        """
        span = Span(dict(fields))
        self._write(logging.DEBUG, f"{event}.start", fields)
        started = time.perf_counter()
        try:
            yield span
        except BaseException as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            METRICS.observe(event, elapsed_ms, outcome="error")
            METRICS.count(f"{event}.error", error=type(exc).__name__)
            if self._logger.isEnabledFor(logging.ERROR):
                self._logger.error(
                    _compose(
                        f"{event}.error",
                        {
                            "elapsed_ms": elapsed_ms,
                            "error": type(exc).__name__,
                            "message": str(exc),
                            **span.fields,
                        },
                    ),
                    exc_info=True,
                )
            raise
        else:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            METRICS.observe(event, elapsed_ms, outcome="ok")
            METRICS.count(f"{event}.ok")
            self._write(
                logging.INFO,
                f"{event}.ok",
                {"elapsed_ms": elapsed_ms, **span.fields},
            )

    def _write(self, level: int, event: str, fields: Mapping[str, object]) -> None:
        if not self._logger.isEnabledFor(level):
            return  # 详细日志的字符串在这里就不构造了
        self._logger.log(level, _compose(event, fields))


def get_log(name: str) -> Log:
    """取一个模块的日志入口. 传 `__name__`; 非 forgecli 前缀的名字会被挂到根下."""
    if name != ROOT_LOGGER_NAME and not name.startswith(f"{ROOT_LOGGER_NAME}."):
        name = f"{ROOT_LOGGER_NAME}.{name}"
    return Log(logging.getLogger(name))
