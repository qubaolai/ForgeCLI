# 2026-07-02：ModelRouter 与候选选择

## 今日目标

实现 `ModelTierConfig`、`ModelTierCandidate` 和 `ModelRouter`，让 gateway 可以按档位候选、模型能力、凭证可用性和 priority 选择具体 provider/model。

## 开发指导

- 定义内置档位：
  - `fast`：低延迟、低成本，适合标题、摘要、compact 和轻量分类。
  - `smart`：高能力或推理模型，适合规划、review、复杂分析。
  - `default`：兜底档位，必须至少有一个候选。
- 实现候选过滤：
  - provider 未注册则过滤或返回明确配置错误。
  - 模型不存在则过滤。
  - 模型能力不足则过滤，例如 context window、structured output、thinking。
  - provider/model 被 allowlist 禁用则过滤。
- 实现 selection 策略的 MVP 版本：
  - `first_available` 按 priority 选择。
  - 其他策略可以先保留枚举并返回未实现错误。
- 实现 fallback 语义：
  - `current_model` 不切换模型。
  - `explicit_model` 默认不 fallback。
  - `tier` 可在同档候选中按 priority 尝试。
  - 档位升档只保留接口，不在今日默认启用。
- `ModelRouteResult` 必须包含 provider、model、tier、selection_reason 和 fallback_chain。

## 非目标

- 不实现真实 provider retry。
- 不实现 provider health 熔断。
- 不实现 lowest cost、lowest latency 或 balanced 策略。

## 最终产物

- `ModelTierConfig` 和 `ModelTierCandidate`。
- `ModelRouter`。
- `ModelRouteRequest` / `ModelRouteResult`。
- 单元测试覆盖 priority、能力过滤、无可用候选和 fallback_chain。

## 验收重点

- 路由只读 catalog，不直接解析 TOML。
- fallback 是有界序列，不能在错误处临时无限重算。
- thinking 参数必须在模型能力不支持时被拒绝或按明确策略处理，不能静默忽略。

## 验收命令

```bash
make ci
poetry run pytest tests
```
