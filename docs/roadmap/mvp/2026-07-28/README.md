# 2026-07-28：工具与审批集成

## 今日目标

把 ToolRequest、Policy、Approval、ToolRuntime 串成完整链路。

## 开发指导

- readonly 工具可直接执行。
- write/destructive/external 根据模式和配置审批。
- 审批结果进入事件日志。

## 最终产物

- 工具调用完整流程。
- 工具调用事件。
- 审批集成测试。

## 代码验收

我会按 chat/plan/act 分别验证工具权限，重点检查 plan 模式不能执行写工具。

