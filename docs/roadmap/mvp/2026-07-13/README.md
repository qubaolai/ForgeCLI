# 2026-07-13：AgentWorkflow 接口

## 今日目标

实现 `AgentWorkflow` 抽象，明确 workflow 只能返回意图，不能执行副作用。

## 开发指导

- 定义 `WorkflowInput`、`WorkflowResult`、`ToolRequest`、`PlanUpdate`。
- `AgentTurnService` 调用 workflow。
- workflow 不直接写事件、不直接执行工具。

## 最终产物

- `AgentWorkflow` 接口。
- workflow contract 测试。
- 副作用边界说明。

## 代码验收

我会检查 workflow 是否没有绕过应用服务，是否能未来替换为 LangGraph adapter。

