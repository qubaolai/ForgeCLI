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
- [ADR-0012：完善统一 LLM 网关运行时自身能力](adr/2026-07-12-0012-完善统一LLM网关运行时自身能力.md)：补齐重试、缓存、thinking 方言、分词器、成本、熔断、预算与观测，并冻结模型级 thinking 与应用级网关配置归属。
- [ADR-0013：采用分层 Shell 命令安全控制架构](adr/2026-07-29-0013-采用分层Shell命令安全控制架构.md)：定义 Shell 方言解析、脚本分析、分类器 fail-safe、阻塞式 ASK、受保护路径和渐进式授权边界。
- [ADR-0014：采用启动探测与按执行实例创建的跨平台沙箱架构](adr/2026-07-29-0014-采用启动探测与按执行实例创建的跨平台沙箱架构.md)：保留沙箱生命周期、能力自测和平台路径保护约束；Provider 选型已被 ADR-0019 替代。
- [ADR-0015：采用首次破坏性写入屏障与独立工作区恢复机制](adr/2026-07-30-0015-采用首次破坏性写入屏障与独立工作区恢复机制.md)：定义不依赖 Git 的写前恢复屏障、分层快照、内容寻址恢复存储、冲突处理和恢复预算。
- [ADR-0016：采用统一 Agent 运行事件流驱动终端过程展示](adr/2026-08-03-0016-采用统一Agent运行事件流驱动终端过程展示.md)：定义模型、思考摘要、工具、安全与审批的统一运行事件，以及终端 append-only 渲染和脱敏边界。
- [ADR-0017：采用井号入口提供人工 Shell 模式](adr/2026-08-04-0017-采用井号入口提供人工Shell模式.md)：定义单独 `#` 的人工终端接管、TTY 信任边界、PTY/ConPTY 交接、会话隐私与返回 Forge 后的上下文失效。
- [ADR-0018：采用分层且按 Turn 冻结的系统提示词编译机制](adr/2026-08-07-0018-采用分层且按Turn冻结的系统提示词编译机制.md)：定义主 Agent 提示词的 application 层编译、信任分层、按 turn 冻结、`FORGE.md` 注入、Prompt Cache 和安全分类器隔离。
- [ADR-0019：采用 Forge 自管跨平台沙箱 Provider](adr/2026-08-10-0019-采用Forge自管跨平台沙箱Provider.md)：定义 Seatbelt、bubblewrap、NoSandbox 的统一 Provider、自测、临时实例和降级边界。
- [ADR-0020：采用 LLM 补充安全评估与人工一次性直接授权](adr/2026-08-10-0020-采用LLM补充安全评估与人工一次性直接授权.md)：定义 auto 解析不完整时的隔离分类器、本地合并规则，以及完整展示后的人类本次直接授权。
- [ADR-0021：采用失败向安全的默认值与端到端事实接线](adr/2026-08-10-0021-采用失败向安全的默认值与端到端事实接线.md)：修正表外默认值的方向、只声明一半的安全事实，以及声称可恢复而实际不可恢复的检查点。
- [ADR-0022：采用项目级持久化的计划与待办清单](adr/2026-08-17-0022-采用项目级持久化的计划与待办清单.md)：定义计划与待办的分离、项目级落盘位置、五个不经审批的专用工具、固定计划模板和会话加载方式。
- [ADR-0023：采用回合边界的计划评审与模式升档](adr/2026-08-17-0023-采用回合边界的计划评审与模式升档.md)：定义工具声明的回合边界停顿、补充/拒绝/同意/同意并执行四选一，以及 `PLAN → ACCEPT_EDITS` 的显式升档边界。
- [Agent Shell 安全红蓝对抗测试方案](security/2026-07-30-Agent-Shell安全红蓝对抗测试方案.md)：定义跨平台攻击语料、失败注入、路径绕过、授权滥用和沙箱边界测试。
- [adr/README.md](adr/README.md)：架构决策记录，说明重要决策的背景、备选方案、影响和验收标准。
- [roadmap/README.md](roadmap/README.md)：按阶段和日期拆分的开发排期、每日目标和代码验收要求。
- [roadmap/backlog.md](roadmap/backlog.md)：MVP 之后的后续迭代 backlog，汇总各文档中「MVP 暂不实现 / 接口预留」的能力及其目标阶段。

## 核心结论

- ForgeCLI 是 conversation-first 的 CLI Agent，不是一次性 goal runner。
- 长任务能力通过 session resume、event log、state snapshot、context compaction 实现。
- Agent 自治程度由 mode policy 控制，默认 accept_edits（改文件自动、命令确认），按需进入 auto / full_access。
- Agent 主循环采用受控 ReAct：模型只产出意图，副作用由 ForgeCLI 控制面执行。
- 所有 LLM 调用必须经过统一 `LlmGateway`，统一执行 token/成本计量、缓存、重试、熔断、预算与审计摘要。
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
