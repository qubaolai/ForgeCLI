# ForgeCLI MVP 阶段滚动开发计划

## 1. 周期

- 起始日期：2026-06-17
- 当前滚动计划结束日期：2026-07-10
- 当前滚动计划范围：2026-06-29 至 2026-07-10，共 10 个工作日
- 阶段目标：根据 ADR-0011 落地统一 LLM 网关 MVP 闭环，让 ForgeCLI 的模型调用从 stub 演进为可选择、可计量、可测试、可接真实 provider，并具备 streaming、tool calling 和缓存能力。

本目录采用滚动排期。已完成日期保留历史记录；07-01 至 07-10 聚焦 LLM gateway MVP 闭环，必须完成模型选择、凭证、计量、AgentTurn、真实 provider、streaming、tool calling 和缓存。Agent 主循环、完整 ToolRuntime、MCP、Skills、完整预算治理、熔断和企业治理继续留到后续 MVP 或大版本切片。

## 2. 当前实现基线

当前代码已经具备：

- `src/forgecli + tests` 布局。
- Typer/Rich 单入口 CLI：裸 `forge` 进入 REPL，`--help` 和 `--version` 保留。
- `IntentRouter` 与 slash command registry。
- `application/config` 下的 `ConfigService`、`EffectiveConfig` 和 TOML store port。
- `application/llm/config` 下的 LLM 配置值对象、service 和 store port。
- `infrastructure/config` 与 `infrastructure/llm/config` 下的 TOML 适配器。
- `application/session` 与 `infrastructure/session` 下的事件存储、state 快照、session catalog 和 resume service。
- `application/agent_turn` 下的 `AgentTurnService` stub。
- `/status`、`/resume`、`/chat`、`/plan`、`/act` 等交互式入口的最小集成。

当前阶段性取舍：

- `/config` 已能管理供应商和模型参数；本阶段补齐当前模型和按用途显式模型覆盖的运行时语义与编辑入口（07-09）。
- 当前 MVP 不维护 `forge chat/status/models/config/resume` 这类 Typer 业务子命令；业务能力优先通过 REPL 内 slash command 暴露。
- 所有模型调用必须经过统一 `LlmGateway`，`AgentTurnService`、`AgentLoop` 和 CLI 不得直接依赖具体供应商 SDK。
- 默认测试不得访问真实网络或真实模型；真实 provider adapter 只能在显式配置凭证后手动验证。
- 本阶段按日完成非流式 `complete`、模型解析、usage/error/credential 边界、取消接线、凭证级 retry、`complete_structured`、AgentTurn 集成、OpenAI provider、streaming、tool calling 和响应缓存；熔断和完整预算扣减进入后续 backlog。
- AgentLoop（ADR-0010）若未落地，本阶段以 `AgentTurnService` 直连 gateway，作为偏差记录处理，不阻塞 gateway 边界验证。

## 3. LLM 网关阶段范围

本阶段必须包含：

- `LlmGateway`、`ModelProvider`、`ModelRequest`、`ModelResponse`、`ModelUsage` 等核心端口和 DTO。
- `FakeModelProvider`，用于 deterministic 单测和 REPL 集成测试。
- `ProviderRegistry`，只允许代码级注册 provider adapter。
- `ModelSelection`：`current_model` 与 `explicit_model` 两类选择。
- `ModelCatalogService` 运行时只读视图和配置覆盖合并。
- `ModelSelectionResolver`，负责当前模型与按用途显式模型覆盖的解析及 catalog 校验。
- `CredentialResolver` / credential ref 边界，确保明文 key 不进入配置、事件、日志或 fixture。
- `TokenEstimator` 近似实现、usage/cost 计量草稿和错误归一化。
- `complete_structured` 的 schema 校验最小能力。
- `AgentTurnService` 通过 gateway 获取 assistant message，并由 `AgentTurnService` 写入模型调用摘要和 usage 事件。
- `cancel_token` 从 `AgentTurnService` 到 gateway / provider 的取消接线（非流式协作式取消 + adapter 在途中止）。
- 同一 provider/model 内有界的凭证级 retry 最小运行时路径（默认测试用 fake transport）。
- `/config` 编辑当前模型、用途模型覆盖和具体模型的 thinking mode / effort。
- OpenAI provider adapter 的最小实现或清晰接口落点，默认测试不打网络。
- OpenAI provider adapter、streaming chunk 归一化、tool calling schema 转换和 tool result 回填。
- `LlmCacheController` 的 prompt 标注与可选响应缓存。

本阶段（当前 LLM 网关滚动切片）不包含。注意区分两类：

留待后续 MVP 滚动切片（仍属 MVP，不进入 backlog）：

- Agent 主循环（受控 ReAct）、模式策略、完整 ToolRuntime 与审批、上下文压缩与记忆、Sub-Agent 等 Agent 主功能。

默认排在 MVP 之后（见 [后续迭代 Backlog](../backlog.md)）：

- `RateLimiter`、`ProviderHealthRegistry` 熔断的真实策略。
- 完整 `BudgetGuard` 扣减、企业成本策略和跨 session 用量统计。
- 可观测性聚合。

## 4. 当前滚动计划拆分

| 日期 | 目标 |
| --- | --- |
| 2026-06-29 | LLM gateway 边界冻结：核心端口、DTO（全字段冻结）、完整 origin 枚举和错误类型 |
| 2026-06-30 | `FakeModelProvider`、provider registry、既有 llm 切片并轨和 `LlmGateway.complete` happy path |
| 2026-07-01 | 模型选择契约、移除旧档位类型和模型目录运行时视图 |
| 2026-07-02 | `ModelSelectionResolver`、当前模型/用途覆盖和能力校验 |
| 2026-07-03 | 凭证解析、安全边界和 provider 错误归一化 |
| 2026-07-06 | `TokenEstimator`、usage/cost 计量草稿和 context window 校验 |
| 2026-07-07 | `complete_structured`、`AgentTurnService` 集成和 `cancel_token` 接线 |
| 2026-07-08 | OpenAI adapter、凭证级 retry 和 provider 请求闭环 |
| 2026-07-09 | streaming 与 tool calling 归一化及 AgentTurn 回填 |
| 2026-07-10 | prompt/response cache、MVP 集成验收和文档收口 |

## 5. 每日验收格式

每天结束时需要提交：

- 今日完成的代码范围。
- 关键文件列表。
- 运行过的测试命令和结果。
- 与 ADR-0011 不一致的地方。
- 是否出现 gateway 直接落盘、application import 具体 SDK、凭证进入事件/日志/fixture 等边界问题。
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
- [2026-07-02](2026-07-02/README.md)
- [2026-07-03](2026-07-03/README.md)
- [2026-07-06](2026-07-06/README.md)
- [2026-07-07](2026-07-07/README.md)
- [2026-07-08](2026-07-08/README.md)
- [2026-07-09](2026-07-09/README.md)
- [2026-07-10](2026-07-10/README.md)

## 7. 2026-07-20 ADR-0012 配置与 CLI 收口

在 07-10 历史滚动计划完成后，按 ADR-0012 增补以下验收口径：

- thinking mode / effort 跟随具体模型，保存到应用级 `llm.toml` 的模型条目；
  `ModelRequest` 不提供显式覆盖。当前模型与用途覆盖继续保存到项目级 `forge.toml`。
- cache / circuit breaker / retry 统一为应用级网关配置，保存到 `llm.toml`，并在
  `/config` 提供查看与编辑入口。
- 输入框下方右侧持续显示当前项目模型与该模型的 thinking，修改或切换模型后重新渲染即刷新。
- ADR-0011、ADR-0012、详细设计、实现与离线测试使用同一配置归属和字段口径。
