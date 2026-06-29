# 2026-07-08：OpenAI-compatible Adapter 与凭证级 Retry 最小路径

## 今日目标

实现 OpenAI-compatible provider adapter 的最小落点，并补上「凭证 -> 候选」的有界 retry 最小运行时路径，让 ADR-0011 §12 / §18 的可用性处理在 MVP 内有真实落点（默认测试不打网络）。

## 开发指导

- 实现 OpenAI-compatible provider adapter 最小版本：
  - adapter 只做协议映射，不读写 session、配置、事件或预算。
  - 凭证由 `CredentialResolver` 注入，adapter 不自行枚举或切换 key。
  - 默认测试使用 fake transport，不访问真实网络。
  - 未配置凭证时直接返回归一化 auth/config 错误，不发起请求。
  - 把 `ModelRequest.cancel_token` 落到真实在途请求：收到取消时中止底层 HTTP，不等待自然超时（承接 07-07 的取消链路）。
- 实现凭证级 retry 最小路径（承接 07-03 的 `CredentialPool` 接口）：
  - auth 失败 / 429 / quota 时，先尝试同 provider 的其他 `credential_refs`。
  - 每层都有上限，受 provider `max_retries` 约束，retry 次数写入安全摘要。
  - `mark_failed(retry_after)` 的凭证在冷却期内不再被选中；`mark_succeeded` 后恢复。
- 候选级 / 档位级 fallback 按 07-02 预计算的 `fallback_chain` 顺序消费（`tier` 或 `explicit_model` 且 `allow_fallback=true` 时）；`current_model` 只做凭证级重试，不切换模型。
- 用 fake transport 模拟 auth/429/timeout/connection failed，验证 retry 与 fallback 的有界性，不依赖真实 API。

## 非目标

- 不要求 CI 访问真实 OpenAI-compatible API。
- 不实现 provider health registry / 熔断（保留接口，记入 backlog）。
- 不实现 response cache / prompt cache。
- 不实现 lowest_cost / lowest_latency / balanced 选择策略。

## 最终产物

- OpenAI-compatible provider adapter 最小落点（fake transport 可测）。
- 凭证级 retry 与候选 fallback 的有界运行时路径。
- 真实在途请求的取消落点。
- fake transport 下的 auth/429/timeout/fallback 单元测试。

## 验收重点

- provider SDK 只出现在 adapter / infrastructure，application 与 AgentTurn 不 import。
- 凭证不写入 config、event、fixture、日志；retry 不无限切换 key。
- fallback 遵循「凭证 -> 候选 -> 档位」固定有序、各层有上限。
- 取消能真正中止在途 HTTP 请求。
- 默认无网络测试通过。

## 验收命令

```bash
make ci
poetry run pytest tests
```
