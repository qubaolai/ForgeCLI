# 2026-06-25：领域模型第二批

## 今日目标

实现 `Plan`、`PlanStep`、`ModePolicy`、`ApprovalRequest` 的领域模型。

## 开发指导

- `Plan` 支持 pending/in_progress/completed/blocked/skipped。
- `ModePolicy` 至少覆盖 chat/plan/act。
- `ApprovalRequest` 能表达风险、动作和审批状态。

## 最终产物

- Agent、Policy 相关领域模型。
- 状态转换测试。
- plan 模式禁止写操作的策略测试。

## 代码验收

我会重点检查模式策略是否能约束工具调用，以及状态转换是否不会产生非法中间态。

