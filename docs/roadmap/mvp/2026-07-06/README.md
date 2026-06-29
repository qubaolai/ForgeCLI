# 2026-07-06：Token 估算与 Usage / Cost 计量草稿

## 今日目标

补齐 gateway 调用前后的计量边界：请求前 token 估算、无 usage 时的 estimated usage、usage/cost 草稿。计量草稿只由 gateway 产出，落盘边界仍在 `AgentTurnService`（07-07 集成）。

## 开发指导

- 实现 `TokenEstimator` 近似版本：
  - 覆盖 system prompt、messages、tools schema 和预期 max output tokens。
  - 按 provider/model 选分词器：优先精确，缺失时退化为字符或经验比例近似。
  - 估算结果必须标记 `estimated`。
  - 两个用途：① 判断是否超出所选模型上下文窗口（在调用 provider 前拦截，触发 compact 或换更大窗口候选）；② 给后续 `BudgetGuard` 做请求前裁决（MVP 内 guard no-op）。
- 实现 `UsageRecordDraft`：
  - 字段与 `UsageRecord` 一致，但尚未分配最终事件顺序，也不表示已持久化。
  - gateway 只返回草稿，最终事件顺序和落盘由 `AgentTurnService` 负责（07-07）。
  - provider 缺失 usage 时用 `TokenEstimator` 估算并标记 `estimated=true`，真实用量与估算用量必须可区分。
- 实现 `CostEstimator` MVP：
  - 价格来自模型目录（07-01 的 catalog 视图）。
  - 价格缺失时 `estimated_cost=None`，不阻塞调用，在 usage 汇总中标记价格未知。
- 把 token 估算挂到 `LlmGateway.complete` 调用前：context window 超限在调用 provider 前归一化为 `ModelContextOverflowError`（错误类型来自 07-03）。

## 非目标

- 不实现真实预算扣减（`BudgetGuard` 仍 no-op）。
- 不实现 usage 落盘（由 07-07 的 `AgentTurnService` 负责）。
- 不实现 streaming usage delta。

## 最终产物

- `TokenEstimator` MVP。
- `UsageRecordDraft` 与 `CostEstimator` MVP。
- 调用前 context window 校验。
- token 估算、estimated usage、cost 缺失标记的单元测试。

## 验收重点

- usage 缺失时 gateway 必须返回 estimated usage 并标记 `estimated=true`。
- context window 超限在调用 provider 前被拦截。
- 价格缺失不阻塞调用，且在汇总中标记。
- gateway 仍然不直接写事件、state 或 usage 文件，只返回草稿。

## 验收命令

```bash
make ci
poetry run pytest tests
```
