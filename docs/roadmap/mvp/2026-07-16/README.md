# 2026-07-16：BuiltinWorkflow Act

## 今日目标

实现 act 模式下的工具请求意图。

## 开发指导

- act 可以返回 `ToolRequest`。
- 所有 ToolRequest 必须经过 Policy 和 Approval。
- 初期工具可使用 mock runtime。

## 最终产物

- act workflow。
- ToolRequest contract 测试。
- act 与 plan 行为差异测试。

## 代码验收

我会检查 act 是否只提出工具意图，不直接执行工具，审批链路是否已预留。

