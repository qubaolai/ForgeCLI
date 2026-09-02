"""开发者诊断读模型 (ADR-0035): 把三份现场读数拼成一份可展示的视图.

三份读数各自回答一个问题:

- `logging`: 日志写在哪, 开到多细. 排查的第一步永远是先找到日志文件.
- `gateway`: 按 provider/model 的调用数, 错误分类, 延迟分桶, cache 命中
  (`InProcessGatewayMetrics`, ADR-0012 §9). 回答"模型侧慢不慢, 在不在报错".
- `stages`: 通用计数与耗时分位数 (`METRICS`). 回答"一轮的时间花在哪一段".

住在 interfaces: 它只做拼装与展示, 没有任何业务判断. 唯一消费者是
`GET /api/v1/diagnostics`.
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

__all__ = ["diagnostics_report"]

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
        # 采集关掉时 stages 是空的. 单独给一个 telemetry 段, 否则"没有阶段耗时"会被
        # 读成"这次运行什么都没干", 而实际是开关关着 (配置项 telemetry.enabled).
        "telemetry": {"enabled": METRICS.enabled},
        "stages": METRICS.snapshot(),
    }
    if gateway is not None:
        report["gateway"] = gateway.snapshot()
    return report
