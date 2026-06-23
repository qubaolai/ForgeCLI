# ForgeCLI MVP 阶段滚动开发计划

## 1. 周期

- 起始日期：2026-06-17
- 当前滚动计划结束日期：2026-07-01
- 当前滚动计划范围：2026-06-25 至 2026-07-01，共 5 个工作日
- 阶段目标：基于当前已实现的单入口 CLI shell、slash command router、交互式配置菜单和 LLM 配置切片，收敛为可测试、结构清晰、可持久化的 MVP 基线。

本目录采用滚动排期。已完成日期保留历史记录；未来一周只描述当前实现可承接的任务，不把远期理想设计直接压到当前阶段。

## 2. 当前实现基线

当前代码已经具备：

- `src/forgecli + tests` 布局。
- Typer/Rich 单入口 CLI：裸 `forge` 进入 REPL，`--help` 和 `--version` 保留。
- `IntentRouter` 与 slash command registry。
- `interfaces/cli` 下的 CLI 命令、菜单和 presenter。
- `application/config` 下的 `ConfigService`、`EffectiveConfig` 和 TOML store port。
- `application/llm/config` 下的 LLM 配置值对象、service 和 store port。
- `infrastructure/config` 与 `infrastructure/llm/config` 下的 TOML 适配器。

当前阶段性取舍：

- 配置文件默认仍走用户级 `~/.forge` 或 `FORGE_CONFIG_DIR`，项目级 `.forge/config.toml` 合并稍后接入。
- `/config` 已能管理供应商和自定义模型参数，但这不是模型目录服务。
- 当前 MVP 不再维护 `forge chat/status/models/config/resume` 这类 Typer 业务子命令；业务能力优先通过 REPL 内 slash command 暴露。
- LLM 调用端口只是占位，不接真实 provider adapter。
- session 仍是内存态，尚未写入 `events.jsonl` 和 `state.json`。

## 3. MVP 范围

MVP 必须包含：

- 本地 CLI 会话。
- `events.jsonl` 和 `state.json`。
- `chat`、`plan`、`act` 三种模式。
- 配置模块。
- 模型目录与运行时默认模型选择。
- `AgentWorkflow` 抽象和 `BuiltinWorkflow` stub。
- 基础工具：文件读取、搜索、shell、git、测试命令。
- 工具风险分级和审批。
- session resume。
- context compact。
- 交互式 inspect/status。

MVP 不包含：

- 完整 Multi-Agent。
- AutoGen adapter。
- 真实 MCP server 深度集成。
- Skills 完整生态。
- 企业审计数据库。
- 云端服务。

## 4. 当前滚动周拆分

| 日期 | 目标 |
| --- | --- |
| 2026-06-25 | 单入口收敛、结构整理收尾、交互式模型目录与运行时默认模型选择 |
| 2026-06-26 | 启动目录信任确认、`/add-dir` 添加可操作目录 |
| 2026-06-27 | 会话事件存储骨架、state 快照和 `/status` 集成 |
| 2026-06-29 | `AgentTurnService` stub、REPL 与 session/event store 解耦 |
| 2026-06-30 | 配置作用域收敛：用户配置 + 项目配置合并，继续复用 `/config` |
| 2026-07-01 | 本周集成验收、`/resume` 最小入口和下阶段工具系统准备 |

## 5. 每日验收格式

每天结束时需要提交：

- 今日完成的代码范围。
- 关键文件列表。
- 运行过的测试命令和结果。
- 与当前实现基线不一致的地方。
- 保留的阶段性取舍和明日建议。

## 6. 日期目录

- [2026-06-17](2026-06-17/README.md)
- [2026-06-18](2026-06-18/README.md)
- [2026-06-19](2026-06-19/README.md)
- [2026-06-22](2026-06-22/README.md)
- [2026-06-23](2026-06-23/README.md)
- [2026-06-24](2026-06-24/README.md)
- [2026-06-25](2026-06-25/README.md)
- [2026-06-26](2026-06-26/README.md)
- [2026-06-27](2026-06-27/README.md)
- [2026-06-29](2026-06-29/README.md)
- [2026-06-30](2026-06-30/README.md)
- [2026-07-01](2026-07-01/README.md)
