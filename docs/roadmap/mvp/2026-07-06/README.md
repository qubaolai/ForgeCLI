# 2026-07-06：Token 估算与 Usage / Cost 计量草稿

## 今日目标

实现 gateway 调用前后的 token、usage 和 cost 计量边界，并把上下文窗口校验与已选模型绑定，为 AgentTurn 集成提供可落盘的计量草稿。

## 开发指导

- 实现 `TokenEstimator` 近似版本，覆盖 text message、system prompt、tool schema 和历史消息的输入估算。
- 实现 `UsageRecordDraft`：保留 request_id、provider、model、origin、usage、estimated、latency 和 cost 摘要。
- provider 返回 usage 时使用真实值；缺失时由 `TokenEstimator` 估算并标记 `estimated=true`。
- 在 gateway 调用 provider 前执行 context window 预检查；超限返回 `ModelContextOverflowError`，不自动换模型。
- 实现 `CostEstimator` MVP；价格缺失时返回 `estimated_cost=None`，不阻塞模型调用。
- 明确 gateway 只返回 usage/cost 草稿，最终事件顺序和落盘仍由 07-07 的 `AgentTurnService` 负责。

## 非目标

- 不实现真实预算扣减，`BudgetGuard` 仍为 no-op。
- 不实现 usage 落盘和 streaming usage delta。
- 不实现 provider retry、熔断、tool calling、缓存和完整上下文压缩。

## 最终产物

- `TokenEstimator` MVP。
- `UsageRecordDraft` 和 `CostEstimator` MVP。
- 已选模型的 context window 前置校验。
- 真实 usage、estimated usage、价格缺失和超限错误测试。

## 验收重点

- usage 缺失时返回 estimated usage，且标记 `estimated=true`。
- context window 超限在 provider 调用前被拦截。
- 价格缺失不阻塞调用，并在草稿中明确标记。
- gateway 不直接写事件、state 或 usage 文件。

## 验收命令

```bash
make ci
poetry run pytest tests
```
