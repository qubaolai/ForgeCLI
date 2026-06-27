# ForgeCLI 设计文档索引

ForgeCLI 的目标是建设一个企业级、生产可用、本地优先的对话式软件工程 CLI Agent。用户通过持续对话推进任务，Agent 根据当前模式在解释、规划、执行、验证、反思之间切换，而不是简单输入一个目标后完全自动运行。

## 文档列表

- [01-overview-design.md](01-overview-design.md)：系统概要设计，说明产品定位、核心原则、总体架构、能力边界和演进路线。
- [02-detailed-design.md](02-detailed-design.md)：系统详细设计，说明领域模型、事件存储、上下文、记忆、工具、MCP、Skills、Agent Runtime 等实现细节。
- [03-delivery-plan.md](03-delivery-plan.md)：开发周期、阶段目标、里程碑、验收口径和版本路线。
- [04-engineering-standards.md](04-engineering-standards.md)：生产级工程流程规范，包括需求、设计、编码、测试、发布、安全、运维和质量门禁。
- [05-acceptance-standards.md](05-acceptance-standards.md)：代码验收标准，说明结构、语法、逻辑、安全、测试和验收报告格式。
- [ADR-0010：采用受控 ReAct 作为 Agent 主循环架构](adr/2026-07-01-0010-采用受控ReAct作为Agent主循环架构.md)：定义受控 reason/act/observe 循环、workflow 边界、事件和恢复策略。
- [ADR-0011：采用统一 LLM 调用网关支持多供应商](adr/2026-07-01-0011-采用统一LLM调用网关支持多供应商.md)：定义统一 gateway、provider adapter、usage/cost/token 计量和错误归一化。
- [adr/README.md](adr/README.md)：架构决策记录，说明重要决策的背景、备选方案、影响和验收标准。
- [roadmap/README.md](roadmap/README.md)：按阶段和日期拆分的开发排期、每日目标和代码验收要求。

## 核心结论

- ForgeCLI 是 conversation-first 的 CLI Agent，不是一次性 goal runner。
- 长任务能力通过 session resume、event log、state snapshot、context compaction 实现。
- Agent 自治程度由 mode policy 控制，默认从保守的 chat/plan 开始，按需进入 act/auto。
- Agent 主循环采用受控 ReAct：模型只产出意图，副作用由 ForgeCLI 控制面执行。
- 所有 LLM 调用必须经过统一 `LlmGateway`，为 token 计量、成本控制、限流和审计预留稳定入口。
- 本地存储首选 `jsonl + state.json + artifacts`，数据库作为企业版或服务端化增强。
- 架构采用轻量 DDD：领域模型稳定，基础设施可替换。
- Agent 框架采用“自有控制面 + 可替换 workflow adapter”：MVP 用自研轻量 Runtime，V1 优先评估 LangGraph，LangChain 只选择性使用底层组件，AutoGen 留给 V2 Multi-Agent。
- 首版优先 Sub-Agent，不急于引入完整 Multi-Agent；通过 Runtime 抽象保留演进空间。

## 推荐阅读顺序

1. 先读概要设计，确认产品形态、边界和架构方向。
2. 再读 Agent ReAct 与 LLM Provider 两份主架构，确认 Agent 基石和模型调用控制面。
3. 再读详细设计，确认模块职责、数据结构和关键流程。
4. 根据开发周期文档拆任务、排期和定义验收。
5. 按工程规范文档执行研发、评审、测试和发布。
