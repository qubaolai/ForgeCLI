# 2026-07-09：ContextPackage

## 今日目标

实现模型调用前的上下文包结构。

## 开发指导

- 组合系统指令、模式策略、会话摘要、最近消息、计划、相关观察。
- 当前阶段可以先不做复杂检索。
- 上下文构建必须可测试。

## 最终产物

- `ContextManager` MVP。
- `ContextPackage` 数据结构。
- token budget 基础测试。

## 代码验收

我会检查上下文是否由应用服务统一构建，而不是散落在 workflow 或 CLI 中。

