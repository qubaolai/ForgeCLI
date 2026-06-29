# ForgeCLI MVP 阶段滚动开发计划

## 1. 周期

- 起始日期：2026-06-17
- 当前滚动计划结束日期：2026-07-10
- 当前滚动计划范围：2026-06-29 至 2026-07-10，共 10 个工作日
- 阶段目标：根据 ADR-0011 落地统一 LLM 网关最小闭环，让 ForgeCLI 的模型调用从 stub 演进为可路由、可计量、可测试、可接真实 provider 的 application 能力。

本目录采用滚动排期。已完成日期保留历史记录；当前 10 天只规划 LLM gateway 阶段，不把工具系统、MCP、Skills、完整预算治理、缓存、熔断和流式/工具调用深水区提前压入本阶段。原 7 天排期中 07-03（凭证+计量+错误）与 07-07（adapter+集成+文档）负载过重，已拆分并补入凭证级 retry、取消信号、`/config` 档位编辑和既有切片并轨，整体顺延 3 个工作日至 07-10。

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

- `/config` 已能管理供应商和模型参数；本阶段补齐当前模型、用途档位和档位候选模型的运行时语义与编辑入口（07-09）。
- 当前 MVP 不维护 `forge chat/status/models/config/resume` 这类 Typer 业务子命令；业务能力优先通过 REPL 内 slash command 暴露。
- 所有模型调用必须经过统一 `LlmGateway`，`AgentTurnService`、`AgentLoop` 和 CLI 不得直接依赖具体供应商 SDK。
- 默认测试不得访问真实网络或真实模型；真实 provider adapter 只能在显式配置凭证后手动验证。
- 本阶段先完成非流式 `complete`、fake provider、路由、usage/error/credential 边界、取消接线、凭证级 retry 最小路径、`/config` 档位编辑和 AgentTurn 集成；streaming、tool calling、响应缓存、熔断和完整预算扣减进入后续 backlog。
- AgentLoop（ADR-0010）若未落地，本阶段以 `AgentTurnService` 直连 gateway，作为偏差记录处理，不阻塞 gateway 边界验证。

## 3. LLM 网关阶段范围

本阶段必须包含：

- `LlmGateway`、`ModelProvider`、`ModelRequest`、`ModelResponse`、`ModelUsage` 等核心端口和 DTO。
- `FakeModelProvider`，用于 deterministic 单测和 REPL 集成测试。
- `ProviderRegistry`，只允许代码级注册 provider adapter。
- `ModelSelection`：`current_model`、`explicit_model`、`tier` 三类选择。
- `ModelCatalogService` 运行时只读视图和配置覆盖合并。
- `ModelTierConfig`、`ModelTierCandidate`、`ModelRouter`，支持 `fast`、`smart`、`default` 档位候选。
- `CredentialResolver` / credential ref 边界，确保明文 key 不进入配置、事件、日志或 fixture。
- `TokenEstimator` 近似实现、usage/cost 计量草稿和错误归一化。
- `complete_structured` 的 schema 校验最小能力。
- `AgentTurnService` 通过 gateway 获取 assistant message，并由 `AgentTurnService` 写入模型调用摘要和 usage 事件。
- `cancel_token` 从 `AgentTurnService` 到 gateway / provider 的取消接线（非流式协作式取消 + adapter 在途中止）。
- 「凭证 -> 候选 -> 档位」的有界 retry / fallback 最小运行时路径（默认测试用 fake transport）。
- `/config` 编辑当前模型、用途档位、档位候选模型、priority、selection 和 thinking 默认值。
- OpenAI-compatible provider adapter 的最小实现或清晰接口落点，默认测试不打网络。

本阶段（当前 LLM 网关滚动切片）不包含。注意区分两类：

留待 MVP 后续滚动切片（仍属 MVP，必须实现，不进入 backlog）：

- streaming chunk 归一化。
- tool calling schema 转换和 tool result 回填。
- prompt / 响应缓存（`LlmCacheController`）。
- Agent 主循环（受控 ReAct）、模式策略、工具系统与审批、上下文压缩与记忆、Sub-Agent 等 Agent 主功能。

默认排在 MVP 之后（见 [后续迭代 Backlog](../backlog.md)）：

- `RateLimiter`、`ProviderHealthRegistry` 熔断的真实策略。
- 完整 `BudgetGuard` 扣减、企业成本策略和跨 session 用量统计。
- 可观测性聚合。

## 4. 当前滚动计划拆分

| 日期 | 目标 |
| --- | --- |
| 2026-06-29 | LLM gateway 边界冻结：核心端口、DTO（全字段冻结）、完整 origin 枚举和错误类型 |
| 2026-06-30 | `FakeModelProvider`、provider registry、既有 llm 切片并轨和 `LlmGateway.complete` happy path |
| 2026-07-01 | 模型目录运行时视图、`ModelSelection` 和用途档位语义 |
| 2026-07-02 | `ModelRouter`、候选过滤、priority 选择和 fallback 规则 |
| 2026-07-03 | 凭证解析、安全边界和 provider 错误归一化 |
| 2026-07-06 | `TokenEstimator`、usage/cost 计量草稿和 context window 校验 |
| 2026-07-07 | `complete_structured`、`AgentTurnService` 集成和 `cancel_token` 接线 |
| 2026-07-08 | OpenAI-compatible adapter 落点和凭证级 retry / 候选 fallback 最小路径 |
| 2026-07-09 | `/config` 当前模型、用途档位和档位候选编辑 |
| 2026-07-10 | 集成验收矩阵、文档同步、偏差记录和下一阶段 backlog |

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
