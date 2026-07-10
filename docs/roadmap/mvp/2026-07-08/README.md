# 2026-07-08：OpenAI Adapter、凭证级 Retry 与真实请求闭环

## 今日目标

实现 OpenAI provider adapter 的最小生产边界，并把 07-03 定义的凭证解析、错误归一化和取消信号接到真实 provider 请求路径；默认测试不访问网络。

## 开发指导

- adapter 只做统一请求与 OpenAI 请求/响应协议映射，不读写 session、配置、事件或预算。
- 凭证由 `CredentialResolver` 注入，adapter 不自行枚举、记录或切换 key。
- 未配置凭证时直接返回归一化 auth/config 错误，不发起网络请求。
- auth、429、quota 等错误只在同一 provider/model 内尝试其他 credential，次数受 `max_retries` 限制。
- `mark_failed(retry_after)` 的凭证进入冷却，`mark_succeeded` 后恢复；retry 次数写入安全摘要。
- 将 `cancel_token` 落到真实在途请求，取消时中止底层 HTTP，不等待自然超时。
- 使用 fake transport 覆盖 auth、429、timeout、connection failed、取消和 usage 响应，不依赖真实 API。

## 非目标

- 不在 provider/model 之间自动 fallback 或切换。
- 不实现 streaming chunk、tool calling schema、缓存和 provider health。
- 不要求 CI 访问真实 OpenAI API。

## 最终产物

- OpenAI provider adapter 最小落点。
- 同一 provider/model 内凭证级 retry 的有界运行时路径。
- 真实在途请求取消落点。
- fake transport 下的 adapter、retry、错误和取消单元测试。

## 验收重点

- provider SDK 只出现在 adapter/infrastructure，application 与 AgentTurn 不 import。
- 凭证不进入 config、event、fixture、日志或异常；retry 不无限切换 key。
- retry 不改变 provider/model 选择，且每次尝试都有上限。
- 默认无网络测试通过。

## 验收命令

```bash
make ci
poetry run pytest tests
```
