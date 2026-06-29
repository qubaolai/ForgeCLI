# 2026-07-03：凭证解析、安全边界与错误归一化

## 今日目标

补齐 gateway 发起模型调用前后的凭证安全边界和错误归一化。把原计划同日的 token / usage / cost 计量拆到 07-06，让凭证安全和错误分类各自有足够空间。

## 开发指导

- 实现 `CredentialResolver` MVP：
  - 支持 `env:NAME` credential ref。
  - 未配置凭证时真实 provider 不得发起网络请求。
  - 返回错误不得包含 env var 的值。
  - 与 06-30 的 `availability.py` 职责对齐：availability 只判断「是否声明 key 名」，真正解析凭证统一走 `CredentialResolver`。
- 支持 provider credential refs 列表，为后续 key pool 和凭证级 retry（07-08）预留接口；本日只解析与选取，不实现真实 HTTP 重试。
- 预留 `CredentialPool.acquire/release/mark_failed/mark_succeeded` 接口形态，MVP 内可单 lease 直通，不做冷却调度。
- 归一化 provider 错误为统一类型：
  - `ModelAuthError`
  - `ModelRateLimitError`
  - `ModelTimeoutError`
  - `ModelContextOverflowError`
  - `ModelBadRequestError`
  - `ModelProviderInternalError`
  - `ModelResponseParseError`
  - `ModelCancelledError`
- 错误归一化用 06-30 的 fake provider 注错路径驱动测试：fake provider 注入各类原始错误，gateway 归一化为统一类型，且都带 `provider`、`model`、`request_id`，不带 secret。
- 错误处理规则（MVP 落点）：
  - auth / rate limit / context overflow 给用户可行动提示。
  - bad request / parse error 保留安全摘要，不盲目 fallback 掩盖。
  - 实际的「凭证 -> 候选 -> 档位」运行时消费推迟到 07-08，本日只保证错误类型与安全摘要正确。

## 非目标

- 不实现 token 估算、usage/cost 草稿（移到 07-06）。
- 不实现真实预算扣减。
- 不实现 provider health registry / 熔断。
- 不实现跨 credential 的真实 HTTP retry（移到 07-08）。

## 最终产物

- `CredentialResolver` MVP 与 credential refs 选取。
- `CredentialPool` 接口形态（MVP 直通）。
- 统一错误归一化与安全摘要。
- 错误归一化测试与凭证安全测试。

## 验收重点

- 明文 key 不进入配置、事件、日志、异常和测试 fixture。
- 未配置凭证时不发起真实网络请求，返回归一化 auth/config 错误。
- 所有归一化错误都带 `provider`、`model`、`request_id`，不带 secret。
- gateway 仍然不直接写事件、state 或 usage 文件。

## 验收命令

```bash
make ci
poetry run pytest tests
```
