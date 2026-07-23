# ForgeCLI 后续迭代 Backlog（MVP 之后）

## 定位

本文件是 **MVP 阶段显式不实现、留待后续大版本迭代** 的能力的登记表。后续会随排期持续填写；当前只登记 **现有文档中已明确标注「MVP 不实现 / 不进入 MVP / 企业版 / V1 / V2」、且不属于 Agent 主功能** 的条目。

**不进入本表：**

- Agent 主功能：Agent 主循环（受控 ReAct）、模式策略、完整 ToolRuntime 与审批、上下文压缩与记忆、Sub-Agent。这些能力仍属于后续 MVP 滚动切片。

约定：

- 版本与阶段定义见 [03-delivery-plan.md](../03-delivery-plan.md)，本表「目标版本」列与之对齐。
- 新增 MVP 取舍时，先确认它是「后续大版本」而非「后续 MVP 切片」；前者登记到本表并在来源文档标注「→ 见 backlog」，后者排进滚动计划。
- 表格为登记格式，字段可随细化补充。

---

## 1. LLM 网关治理（ADR-0011 排在 MVP 之后 / 企业版）

来源：[ADR-0011](../adr/2026-07-01-0011-采用统一LLM调用网关支持多供应商.md)。MVP 网关范围由 2026-07-01 至 2026-07-10 的滚动计划承载；本表只登记下列治理能力，这些能力以 no-op 或直通预留，排在 MVP 之后。

| 能力 | MVP 取舍 | 来源 | 目标版本 |
| --- | --- | --- | --- |
| `ProviderHealthRegistry` 熔断与半开探测 | MVP 无熔断，逐请求试探 | ADR-0011 §12.1、§17 | Beta+ |
| `BudgetGuard` 实裁决 + `BudgetPolicy` + `BudgetTracker` | `budget_snapshot` 已冻结，guard no-op、不扣减 | ADR-0011 §11.3、§17 | Beta+ |
| 可观测性聚合与 trace span | MVP 不聚合 provider/model 指标 | ADR-0011 §15、§17 | Beta+ |
| 企业托管凭证 / keychain / secret store 凭证源 | MVP 只支持 `env:NAME` | ADR-0011 §7 | 企业版 |
| 企业 allowlist 编辑与插件式 provider 注册 | provider 注册表代码级封闭 | ADR-0011 §6 | 企业版 |
| Embeddings / rerank 等非对话模型 | 非目标；后续须新增 gateway 方法走同一控制面 | ADR-0011 §1 | 待定 |
| 多模态 image / file 内容块 | message schema 已预留扩展点，MVP 只用 text 块 | ADR-0011 §1、§3.3 | 待定 |

`RateLimiter` 客户端限流与并发控制原登记于此（Beta+）：2026-07-16 复核后判定
ForgeCLI 定位为个人单用户 CLI，本地限流阈值相对 provider 真实 429 响应没有
信息优势，已从路线图移除、不再规划实现（见 ADR-0011 §13、ADR-0012 决策 9）。

---

## 2. Workflow Adapter 与完整 Multi-Agent

MVP 的 Agent 主循环、Sub-Agent 用自研轻量 Runtime（`BuiltinAgentLoop`）实现，属 MVP；下列为可替换框架适配与完整 Multi-Agent，明确排在 MVP 之后。

| 能力 | MVP 取舍 | 来源 | 目标版本 |
| --- | --- | --- | --- |
| `LangGraphWorkflowAdapter` 复杂 workflow 试点 | MVP 用自研 `BuiltinAgentLoop`，不暴露 LangGraph checkpoint/schema 为公共协议 | 01-overview §172/§194、03-delivery §56/§113 | V1 |
| 完整 Multi-Agent（AutoGen `FutureAutoGenWorkflowAdapter`） | 仅预留命名，不让代码依赖 AutoGen 的 agent/session/message 模型 | 01-overview §194、02-detailed §265 | V2 |

---

## 3. CLI 业务子命令入口

| 能力 | MVP 取舍 | 来源 | 目标版本 |
| --- | --- | --- | --- |
| Typer 业务子命令 `forge chat/status/config/models/resume` | MVP 单入口，业务能力只走 REPL 内 slash command；脚本友好入口后续重评 | ADR-0007 §4/§58、02-detailed §558、05-acceptance §38 | 后续重评 |
| `forge init` 项目偏好 / 记忆 / 仓库约定初始化 | 保留命令名，MVP 不用于启动对话或配置初始化 | ADR-0007 §29 | 待定 |

---

## 4. 存储后端

| 能力 | MVP 取舍 | 来源 | 目标版本 |
| --- | --- | --- | --- |
| SQLite / 服务端化存储后端 | MVP 用 `jsonl + state.json`，企业审计 / 服务端化再引入 | ADR-0001 §27 | 企业版 / 服务端 |

---

## 关联文档

- [03-delivery-plan.md](../03-delivery-plan.md) — 版本路线与阶段目标，本表目标版本列与之对齐。
- [roadmap/mvp/README.md](mvp/README.md) — 当前 MVP 滚动排期；属 MVP 的后续能力排在这里，不进入本表。
- [ADR-0011 §17](../adr/2026-07-01-0011-采用统一LLM调用网关支持多供应商.md) — LLM 网关 MVP 切片与排在 MVP 之后的治理能力。
