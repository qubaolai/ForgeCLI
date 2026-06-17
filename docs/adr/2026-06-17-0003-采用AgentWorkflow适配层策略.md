# ADR-0003：采用 AgentWorkflow 适配层策略

## 状态

Accepted

## 日期

2026-06-17

## 背景

ForgeCLI 可以使用 LangGraph、LangChain、AutoGen 等 Agent 开发框架，但不能让第三方框架接管会话存储、权限审批、工具执行、事件日志和上下文恢复。

## 决策

定义 `AgentWorkflow` 作为 Agent 编排适配接口。MVP 使用 `BuiltinWorkflow` 打通 chat、plan、act 的最小闭环；后续通过 `LangGraphWorkflowAdapter` 接入复杂状态图；AutoGen 仅作为 V2 Multi-Agent 候选。

## 备选方案

- 直接使用 LangGraph 作为核心：编排能力强，但状态和 checkpoint 容易与自有存储重叠。
- 直接使用 LangChain Agent Executor：接入快，但高层抽象不利于权限和审计。
- 直接使用 AutoGen：适合 Multi-Agent，但不适合 MVP 的本地对话主路径。

## 影响

该方案保留框架能力，同时避免框架绑定。代价是需要维护适配层，并把框架输出统一映射为 ForgeCLI 的 `WorkflowResult`。

## 验收标准

- application 层只依赖 `AgentWorkflow` 接口。
- Workflow 不能直接写文件、执行 shell、写事件或写长期记忆。
- 所有工具请求必须回到 Tool Runtime。
- LangGraph 只能作为 adapter，不作为公共协议。

## 关联文档

- `docs/01-overview-design.md`
- `docs/02-detailed-design.md`

