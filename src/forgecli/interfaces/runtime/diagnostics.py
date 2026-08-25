"""开发者诊断读模型 (ADR-0035): 把三份现场读数拼成一份可展示的视图.

三份读数各自回答一个问题:

- `logging`: 日志写在哪, 开到多细. 排查的第一步永远是先找到日志文件.
- `gateway`: 按 provider/model 的调用数, 错误分类, 延迟分桶, cache 命中
  (`InProcessGatewayMetrics`, ADR-0012 §9). 回答"模型侧慢不慢, 在不在报错".
- `stages`: 通用计数与耗时分位数 (`METRICS`). 回答"一轮的时间花在哪一段".

住在 interfaces: 它只做拼装与展示, 没有任何业务判断. CLI 的 `/diagnostics` 与 Web 的
`GET /api/v1/diagnostics` 读的是同一个函数, 两边不会给出不同的数字.
"""

from __future__ import annotations

import os
import platform
import sys
import time

from forgecli.application.llm.gateway.observability import InProcessGatewayMetrics
from forgecli.shared import __version__
from forgecli.shared.observability.configure import logging_status
from forgecli.shared.observability.metrics import METRICS

__all__ = ["diagnostics_report", "render_diagnostics"]

# 进程启动时刻. 模块首次 import 时取, 与"forge 跑了多久"足够接近.
_STARTED_AT = time.monotonic()


def diagnostics_report(
    gateway: InProcessGatewayMetrics | None = None,
) -> dict[str, object]:
    """当前进程的诊断读数 (纯内存, 不读文件, 不发请求)."""
    status = logging_status()
    report: dict[str, object] = {
        "process": {
            "forge_version": __version__,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "pid": os.getpid(),
            "uptime_seconds": round(time.monotonic() - _STARTED_AT, 1),
        },
        "logging": {
            "configured": status.configured,
            "level": status.level,
            "file": "" if status.file is None else str(status.file),
            "console": status.console,
            "max_value_chars": status.max_value_chars,
        },
        "stages": METRICS.snapshot(),
    }
    if gateway is not None:
        report["gateway"] = gateway.snapshot()
    return report


def render_diagnostics(report: dict[str, object]) -> str:
    """把读数排成终端里能直接读的几段文本.

    刻意不做表格对齐: 这份东西的读者是开发者, 他多半要把它整段贴进 issue 或者 grep
    某个键, 而对齐用的空格会让两件事都变难.
    """
    lines: list[str] = []
    process = _section(report, "process")
    lines.append(
        f"forge {process.get('forge_version')} · python {process.get('python')} · "
        f"pid {process.get('pid')} · 已运行 {process.get('uptime_seconds')}s"
    )
    lines.append(f"platform: {process.get('platform')}")

    logging_view = _section(report, "logging")
    if logging_view.get("configured"):
        lines.append(
            f"日志: level={logging_view.get('level')} "
            f"console={logging_view.get('console')} "
            f"单值上限={logging_view.get('max_value_chars')}"
        )
        lines.append(f"日志文件: {logging_view.get('file')}")
    else:
        lines.append("日志: 未装配 (这个进程没有调用 configure_logging)")

    stages = _section(report, "stages")
    lines.extend(_duration_lines(stages))
    lines.extend(_counter_lines(stages))
    lines.extend(_gateway_lines(_section(report, "gateway")))
    return "\n".join(lines)


def _section(report: dict[str, object], name: str) -> dict[str, object]:
    value = report.get(name)
    return value if isinstance(value, dict) else {}


def _duration_lines(stages: dict[str, object]) -> list[str]:
    durations = stages.get("durations")
    if not isinstance(durations, dict) or not durations:
        return ["", "阶段耗时: (本进程还没有量到任何一段)"]
    lines = ["", "阶段耗时 (ms):"]
    for key, view in sorted(durations.items()):
        if not isinstance(view, dict):
            continue
        lines.append(
            f"- {key}: n={view.get('count')} avg={view.get('avg_ms')} "
            f"p50={view.get('p50_ms')} p95={view.get('p95_ms')} "
            f"max={view.get('max_ms')} total={view.get('total_ms')}"
        )
    return lines


def _counter_lines(stages: dict[str, object]) -> list[str]:
    counters = stages.get("counters")
    if not isinstance(counters, dict) or not counters:
        return []
    lines = ["", "计数:"]
    lines.extend(f"- {key}: {value}" for key, value in sorted(counters.items()))
    return lines


def _gateway_lines(gateway: dict[str, object]) -> list[str]:
    if not gateway:
        return []
    lines = ["", "模型调用 (按 provider/model):"]
    for key, view in sorted(gateway.items()):
        if not isinstance(view, dict):
            continue
        lines.append(
            f"- {key}: calls={view.get('calls')} cache_hits={view.get('cache_hits')} "
            f"retries={view.get('credential_retries')}/"
            f"{view.get('transport_retries')}/{view.get('wait_retries')} "
            f"errors={view.get('errors')}"
        )
        lines.append(f"  延迟分桶: {view.get('latency_buckets')}")
    return lines
