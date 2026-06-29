# 2026-07-10：LLM Gateway 阶段集成验收、文档同步与下一阶段 Backlog

## 今日目标

收敛 LLM gateway 阶段成果，跑通 ADR-0011 §18 的集成测试矩阵，同步设计文档与偏差记录，并明确 streaming、tool calling、缓存、熔断、完整预算治理的下一阶段 backlog。

## 开发指导

- 补齐 gateway 集成测试矩阵（ADR-0011 §18）：
  - fake provider 正常回答。
  - usage 存在时记录真实 usage；缺失时估算并标记 `estimated=true`。
  - `current_model` 路由到配置中的主 Agent provider/model。
  - tier 路由到正确候选 provider/model；同档多候选按 priority 和 selection 选择。
  - 候选能力不足时自动跳过或按 fallback 降级。
  - auth / rate limit / timeout / context overflow 错误被归一化。
  - auth 和 429 可尝试同 provider 其他 credential，且有重试上限。
  - `complete_structured` 能校验 schema，失败返回归一化 parse error。
  - 取消信号能中止在途调用，返回 `interrupted=true` 收尾，不留未关闭 turn。
  - gateway 不直接写事件、state、usage 文件或 session snapshot。
  - 未配置凭证时不发起真实网络请求。
- 更新文档：
  - 如模块边界和 ADR-0011 不一致，先修正设计文档或记录偏差（含「AgentLoop 层暂以 AgentTurnService 直连 gateway」的偏差）。
  - 同步 `docs/02-detailed-design.md`、`docs/03-delivery-plan.md` 与本阶段实际落点。
  - 在 roadmap 中记录下一阶段任务。
- 输出阶段验收报告：完成范围、关键文件、测试命令与结果、与 ADR-0011 的偏差、边界自检（是否出现 gateway 落盘 / application import SDK / 凭证入事件日志 fixture）。

## 下一阶段工作

### 下一个 MVP 滚动切片（仍属 MVP，必须实现）

- streaming chunk 归一化和 interrupted partial。
- tool calling schema 转换、tool result 回填归一化（含 `tool_observation` origin 启用）。
- prompt / response 缓存（`LlmCacheController`）。
- `complete_structured` 的受控解析降级路径与重试上限。

### 排在 MVP 之后（登记到 [后续迭代 Backlog](../../backlog.md)）

- `ProviderHealthRegistry` 熔断、`RateLimiter` 真实限流。
- no-op `BudgetGuard` 演进为真实预算裁决，`BudgetPolicy` / `BudgetTracker` 落地。
- 可观测性聚合与 `/status` 展示 provider / model / usage 诊断摘要。

## 非目标

- 不要求 CI 访问真实 OpenAI-compatible API。
- 不实现完整 tool runtime。
- 不实现 response cache、prompt cache 或 provider health registry。

## 最终产物

- LLM gateway 阶段集成测试矩阵。
- 文档同步与偏差记录。
- 阶段验收报告。
- 下一阶段 backlog。

## 验收重点

- 所有 LLM 调用都经过 `LlmGateway`。
- application 和 AgentTurn 不 import 具体 provider SDK。
- 凭证不写入 config、event、fixture 或日志。
- 每次模型调用都有 `request_id`、provider、model、usage 或 estimated usage。
- 默认无网络测试通过。

## 验收命令

```bash
make ci
poetry run forge --help
printf '/status\nhello\n' | poetry run forge
```

如果 TTY 限制导致管道无法完整驱动 REPL，应以 gateway、provider adapter、AgentTurn 和 session service 的集成测试作为主验收。
