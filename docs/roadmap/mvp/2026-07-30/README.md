# 2026-07-30：Inspect 与 Status

## 今日目标

实现基本的状态查看能力。

## 开发指导

- `forge status` 显示当前 session、模式、计划、预算。
- `forge inspect <session_id>` 显示事件摘要、工具历史、审批历史、错误。
- 输出要适合人工排查。

## 最终产物

- status 命令。
- inspect 命令。
- inspect 输出快照测试。

## 代码验收

我会检查 inspect 是否能帮助排查长任务，不要求美观但必须信息完整、不过度泄露敏感内容。

