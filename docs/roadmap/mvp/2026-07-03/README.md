# 2026-07-03：凭证解析、安全边界与错误归一化

## 今日目标

补齐 gateway 发起模型调用前后的凭证边界和错误归一化，为后续 OpenAI adapter 与凭证级 retry 提供稳定端口。

## 开发指导

- 实现 `CredentialResolver` MVP：支持 `env:NAME` credential ref，返回错误不得包含凭证值。
- 实现 `CredentialPool.acquire/release/mark_failed/mark_succeeded` 接口形态；今日允许单 lease 直通，不实现真实 retry 调度。
- provider adapter 不直接读取环境变量、不写配置或 session；凭证由 gateway 注入到 provider request 或调用上下文。
- 将 provider 原始错误归一化为 auth、rate limit、timeout、context overflow、bad request、provider internal、parse、cancelled 等统一类型。
- 为未配置凭证、凭证引用非法、provider 错误和 metadata 脱敏补测试。
- 明确 gateway 只返回错误和安全摘要，不直接写事件、state 或 usage 文件。

## 非目标

- 不实现 token/usage/cost 计量。
- 不实现跨 credential 的真实 HTTP retry，推迟到 07-08。
- 不实现 provider health、熔断、streaming、tool calling 和缓存。

## 最终产物

- `CredentialResolver` MVP。
- `CredentialPool` 端口和单 lease 实现。
- 统一 provider 错误与安全摘要。
- 凭证安全、错误归一化和未配置凭证测试。

## 验收重点

- 明文 key 不进入配置、事件、日志、异常和测试 fixture。
- 未配置凭证时不发起真实网络请求。
- 归一化错误带 provider、model、request_id，但不带 secret。
- credential 选择不改变已经解析出的 provider/model。

## 验收命令

```bash
make ci
poetry run pytest tests
```
