# ForgeCLI 开发周期与阶段目标

## 1. 交付策略

ForgeCLI 按企业级产品节奏推进，采用“核心闭环先行、能力逐步增强、每阶段可验收”的方式。

总体周期建议：16 到 20 周。

版本路线：

- P0：立项与架构冻结。
- MVP：可对话、可恢复、可受控执行。
- Alpha：工具/MCP/Skills 初步可用。
- Beta：Sub-Agent、Review/Debug、上下文和记忆完善。
- GA：安全、稳定性、文档、发布和企业流程达标。

## 2. 团队角色

建议最小团队：

- Tech Lead：架构、领域模型、质量门禁。
- Agent Engineer：Agent Runtime、上下文、模式策略。
- Platform Engineer：CLI、存储、工具系统、MCP。
- QA Engineer：测试体系、E2E、回归和发布验证。
- Security Reviewer：权限、命令风险、敏感信息、供应链。
- Technical Writer：用户文档、设计文档、变更日志。

小团队可一人多角色，但职责不能缺失。

## 3. 阶段划分

### Phase 0：产品定义与架构冻结

周期：1 周。

目标：

- 明确 ForgeCLI 是对话式软件工程 CLI。
- 冻结 MVP 边界。
- 确认技术栈、Agent 框架策略、存储方式和领域边界。
- 建立研发规范和质量门禁。

交付物：

- 概要设计。
- 详细设计。
- 开发周期和工程规范。
- ADR 模板。
- Agent 框架选型 ADR。
- LangGraph 最小 POC 结论。
- 初始 backlog。

验收标准：

- 核心设计问题有明确结论。
- 明确 MVP 使用自研轻量 Runtime，LangGraph 通过 adapter 试点。
- MVP 不再包含完整 Multi-Agent、云端平台、团队协同。
- 开发任务可拆分到 1 到 3 天粒度。

### Phase 1：工程骨架与领域内核

周期：2 周。

目标：

- 建立 Python 项目结构。
- 实现核心领域模型。
- 实现本地 session 存储。
- 建立 CLI 基础入口。

主要工作：

- `pyproject.toml`、`poetry.lock`、`src/forgecli + tests` 包结构、测试框架、lint/format/typecheck。
- `Session`、`Message`、`AgentTurn`、`Plan`、`ModePolicy`、`ToolSpec`。
- `AgentWorkflow` 抽象和 `BuiltinWorkflow`。
- `EventStore`：append、read、replay。
- `StateStore`：原子快照写入和恢复。
- 裸 `forge` 交互式入口，以及 `/resume`、`/status` 基础 slash command。

验收标准：

- Python 版本声明为 3.13+，并通过 Poetry 管理依赖。
- MVP 骨架仅创建当前阶段需要的目录和文件。
- 可创建 session。
- 用户输入和 assistant 输出写入 `events.jsonl`。
- `state.json` 可恢复当前模式和摘要。
- 单元测试覆盖核心领域模型。
- CI 可运行 test、lint、typecheck。

### Phase 2：对话循环与模式策略

周期：2 到 3 周。

目标：

- 建立 conversation-first 的 Agent Turn。
- 实现 chat/plan/act 模式。
- 实现 Context Package。
- 接入第一个 LLM Provider。

主要工作：

- `AgentTurnService`。
- `AgentWorkflow` 接入 `AgentTurnService`。
- `IntentRouter`。
- `ModePolicyResolver`。
- `ContextManager`。
- `ModelProvider` 抽象和一个实际 provider。
- LangGraph adapter 技术验证，不作为默认唯一执行路径。
- slash commands：`/help`、`/chat`、`/plan`、`/act`、`/config`、`/models`、`/status`、`/pause`、`/exit`。

验收标准：

- CLI 可持续多轮对话。
- plan 模式只读，不允许写文件。
- act 模式可在审批后执行写操作。
- 模式切换写入事件日志。
- 中断后 resume 能继续对话。

### Phase 3：工具系统与审批

周期：2 到 3 周。

目标：

- 实现统一 Tool Registry。
- 实现基础工具。
- 实现风险分级和审批流。
- 实现 artifacts 保存。

主要工作：

- `ToolRegistry`、`ToolExecutor`、`ToolResultNormalizer`。
- `fs.read_file`、`fs.write_patch`、`search.text`。
- `shell.run`、`git.status`、`git.diff`、`test.run`。
- `ApprovalService`。
- 工具 stdout/stderr 长输出落 artifact。
- 工具事件全量记录。

验收标准：

- Agent 可搜索、读文件、执行测试。
- 写文件和高风险命令受 ModePolicy 约束。
- 审批请求内容清晰。
- 工具失败不破坏 session。
- 可通过 `forge inspect` 查看工具历史。

### Phase 4：上下文压缩与记忆

周期：2 周。

目标：

- 支持长对话上下文压缩。
- 支持项目记忆和用户偏好。
- 支持手动和自动 compact。

主要工作：

- `summary.md` 生成与更新。
- `/compact` 命令。
- `memory/project.md`。
- `memory/user.json`。
- 记忆写入过滤和敏感信息保护。
- Context budget 分配策略。

验收标准：

- 长对话超过阈值自动压缩。
- compact 后继续对话不丢关键状态。
- 项目记忆可被后续 session 引用。
- 敏感信息不会默认写入 memory。

### Phase 5：MCP 与 Skills

周期：2 到 3 周。

目标：

- 接入 MCP server。
- 实现 Skill manifest。
- 将外部能力纳入统一工具和权限系统。

主要工作：

- `McpClientAdapter`。
- `.forge/mcp/servers.toml`。
- MCP tool schema 转换。
- MCP 工具风险包装和事件日志。
- Skill manifest loader。
- Skill trigger matching。
- 内置示例 Skill：`code-review`、`fix-test`。

验收标准：

- 可连接 mock MCP server。
- MCP 工具显示在 `/tools`。
- MCP 工具调用经过审批和日志。
- Skill 可根据用户输入触发。
- Skill 不绕过 Tool Registry。
- LangChain 如被使用，仅限 provider、prompt、parser、splitter 等底层组件。

### Phase 6：Sub-Agent 与高级模式

周期：2 到 3 周。

目标：

- 实现 Sub-Agent Runtime。
- 增加 review/debug/auto 模式。
- 提升复杂任务处理能力。

主要工作：

- `SubAgentRunner`。
- `ResearchAgent`。
- `ReviewerAgent`。
- `DebugAgent`。
- LangGraphWorkflowAdapter 试点复杂 workflow。
- `auto` 模式预算和停止条件。
- Sub-Agent 报告合并。
- 多 Sub-Agent 并行只读探索。

验收标准：

- review 模式优先输出风险、bug、测试缺口。
- debug 模式能分析测试失败并建议修复。
- auto 模式受步骤、时间、工具调用预算限制。
- Sub-Agent 无直接写权限。
- 主控 Agent 能合并 Sub-Agent 结果。

### Phase 7：生产硬化与 Beta

周期：3 周。

目标：

- 完成安全、稳定性、可观测性和真实仓库验证。
- 提供可发布 Beta。

主要工作：

- 命令风险分类完善。
- 路径越权保护。
- 事件日志脱敏。
- OpenTelemetry 可选接入。
- E2E 测试矩阵。
- 文档完善。
- 安装包和发布流程。

验收标准：

- 通过安全测试。
- 通过真实仓库 E2E。
- `forge doctor` 可诊断环境。
- 发布包可安装、可升级、可卸载。
- Beta 用户文档完整。

### Phase 8：GA 发布

周期：1 到 2 周。

目标：

- 达到生产可用发布标准。
- 完成兼容性、稳定性和支持流程。

主要工作：

- 回归测试。
- 性能测试。
- 版本冻结。
- Changelog。
- 已知问题列表。
- 支持和反馈流程。

验收标准：

- P0/P1 bug 清零。
- 核心 E2E 全通过。
- 安装、初始化、恢复、执行、卸载路径验证通过。
- 文档、示例、FAQ 完整。
- 发布版本可追溯。

## 4. 里程碑

| 里程碑 | 周期 | 关键结果 |
| --- | --- | --- |
| M0 | 第 1 周 | 设计冻结、backlog 建立 |
| M1 | 第 3 周 | CLI 骨架、事件存储、状态恢复 |
| M2 | 第 6 周 | 对话循环、chat/plan/act |
| M3 | 第 9 周 | 工具系统、审批、artifacts |
| M4 | 第 11 周 | 上下文压缩、基础记忆 |
| M5 | 第 14 周 | MCP、Skills |
| M6 | 第 17 周 | Sub-Agent、auto/review/debug |
| Beta | 第 18 周 | 真实仓库试用 |
| GA | 第 20 周 | 生产发布 |

## 5. 版本功能边界

### MVP 必须包含

- 本地 CLI 会话。
- `events.jsonl` 和 `state.json`。
- chat/plan/act。
- 基础工具。
- 审批。
- resume。
- context compact。

### Alpha 必须包含

- MCP mock 接入。
- Skill loader。
- project/user memory。
- inspect。
- 基础 E2E。

### Beta 必须包含

- review/debug/auto。
- Sub-Agent。
- 安全策略完善。
- 真实仓库验证。
- 发布包。

### GA 必须包含

- 完整文档。
- 质量门禁。
- 安全审查。
- 版本升级策略。
- 可观测性和诊断。

## 6. 开发节奏

采用两周一个 sprint。

每个 sprint 固定节奏：

- Day 1：计划会，确认目标和验收。
- Day 2-8：开发和自测。
- Day 9：集成测试、文档更新。
- Day 10：Demo、回顾、风险处理。

每个任务进入开发前必须具备：

- 用户价值。
- 范围边界。
- 设计说明。
- 验收标准。
- 测试策略。

## 7. 质量门禁

合并前必须通过：

- 单元测试。
- 集成测试。
- 类型检查。
- lint。
- 安全用例。
- 文档同步。
- 设计变更需 ADR。

发布前必须通过：

- E2E。
- 回归测试。
- 安装测试。
- 升级测试。
- 真实仓库 smoke test。
- 安全审查。

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| Agent 行为不可控 | 误改代码或误执行命令 | mode policy、审批、预算、审计 |
| 长对话状态丢失 | 用户体验差 | jsonl event + state snapshot + summary |
| 工具系统扩展混乱 | 后续难维护 | 统一 ToolSpec 和 Tool Registry |
| MCP 权限过大 | 安全风险 | allowlist、风险包装、审批 |
| Sub-Agent 过早复杂化 | 开发延期 | 首版只做无写权限报告型 Sub-Agent |
| 上下文压缩丢关键信息 | 长任务失败 | 摘要模板、checkpoint、artifact 保留 |
| LLM Provider 锁定 | 后续迁移难 | ModelProvider 抽象 |

## 9. 生产就绪清单

- 所有高风险操作有审批。
- 所有工具调用可审计。
- session 可恢复。
- 日志不泄露敏感信息。
- 默认配置安全。
- CLI 错误信息可理解。
- 用户文档覆盖核心流程。
- 开发者文档覆盖架构和扩展。
- 安装和升级流程稳定。
- 有明确版本号和 changelog。
