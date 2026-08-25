"""可观测性: 结构化日志, 运行上下文与进程内指标 (ADR-0035).

三个模块回答三个不同的问题:

- `log.get_log(__name__)` -> `Log`: **这一次发生了什么.** 一行一条 `事件名 键=值`.
- `context.bind(...)`: **这一行属于谁.** 把 session / turn / step / 调用 id 挂在当前
  执行流上, 日志自动带上, 于是一次故障能顺着 id 串起来读.
- `metrics.METRICS`: **这一类一共花了多久.** 计数与耗时分位数, 供 `/diagnostics` dump.

装配只在进程入口做一次 (`configure.configure_logging`), 业务代码只写日志, 不碰 handler.

任何层都可以 import 本包: 它住在 `shared`, 不依赖 domain / application / infrastructure
中的任何一个, 也不引入第三方依赖 (只用标准库 logging), 所以 `scripts/check_arch.py`
的层间方向与框架隔离两条规则都不会被它破坏.

**包门面不转导出任何东西.** 一律从子模块 import —— `configure` 依赖 `log`, 门面再
急切 import `configure` 就会构成 `observability <-> configure` 的 import 环 (见
scripts/check_arch.py 的环检查).
"""
