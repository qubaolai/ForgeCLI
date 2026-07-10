# 2026-07-10：缓存、MVP 集成验收与文档收口

## 今日目标

完成 `LlmCacheController` 的 prompt/response cache MVP，并跑通 ADR-0011 §18 的完整 LLM gateway 集成矩阵，确认 07-01 至 07-09 的实现可以作为 MVP 网关闭环交付。

## 开发指导

- 实现 prompt cache 控制标注：缓存键必须包含 provider、model、origin、请求参数、工具/schema 摘要和 prompt 版本，不能包含 secret。
- 实现可选 response cache：命中返回 `cached=true`，不重复计真实 usage；chat/act 默认关闭，内部确定性任务可配置开启。
- 缓存只存在于运行时或明确的缓存存储边界，不写入 session event、state 或完整 prompt/response 原文。
- 集成验证：current model、用途覆盖、catalog 能力错误、credential retry、usage/cost、structured output、取消、streaming、tool calling、tool result 和缓存命中/未命中。
- 补齐 gateway 不直接落盘、application 不 import SDK、无凭证不发网络、错误不泄露 secret 的边界测试。
- 同步 ADR-0011、详细设计、MVP 路线图和 backlog，记录 `AgentTurnService` 直连 gateway 的阶段偏差。

## 非目标

- 不实现 ProviderHealthRegistry 熔断、RateLimiter 真实限流、BudgetGuard 实裁决和可观测性聚合；这些继续保留在后续 backlog。
- 不要求 CI 访问真实 OpenAI API。
- 不实现完整 ToolRuntime、MCP、Skills 或 Sub-Agent。

## 最终产物

- `LlmCacheController` prompt/response cache MVP。
- 完整 gateway 集成测试矩阵和安全边界测试。
- ADR、详细设计、路线图和 backlog 同步。
- MVP 网关阶段验收报告和后续任务清单。

## 验收重点

- 所有 LLM 调用都经过 `LlmGateway`。
- 每次模型调用都有 request_id、provider、model、usage 或 estimated usage。
- streaming、tool calling、structured output、取消和缓存均有 deterministic 测试。
- 缓存命中不重复计真实 usage，且不泄露凭证或完整敏感内容。
- 默认无网络测试通过，文档不再把 MVP 能力登记为 backlog。

## 验收命令

```bash
make ci
poetry run forge --help
poetry run forge --version
```
