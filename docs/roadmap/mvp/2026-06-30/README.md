# 2026-06-30：Fake Provider、既有切片并轨与 Gateway Happy Path

## 今日目标

实现 `FakeModelProvider`、代码级 `ProviderRegistry` 和 `LlmGateway.complete` 的最小 happy path，让测试和后续 AgentTurn 集成不依赖真实网络或真实模型。同时把已有的 `application/llm` 切片并轨到新网关抽象，避免出现两套 provider 注册表 / 两套凭证概念。

## 开发指导

- 既有切片并轨（先做，避免后续重复抽象）：
  - 既有 `providers.py` 的 `REGISTRY` 是「id -> `ProviderSpec`」的配置校验注册表，且只含 `deepseek` / `mimo`、无 adapter 绑定。把它扩成（或由新 `ProviderRegistry` 包裹）带 adapter 绑定的统一注册表，并补 `openai`、`local` 两个 provider id。
  - 既有 `availability.py` 的 `EnvProviderAvailability` 与将来的 `CredentialResolver`（07-03）划清职责：availability 只作「provider 是否声明了 key 名」的轻量判断，凭证解析统一走 `CredentialResolver`，不另立第二套凭证概念。
  - 既有 `model_ref.py` 的 `ModelRef`（provider+model）作为 `ExplicitModelSelection` 的内部表示复用，不再新建平行的 provider+model 值对象。
- 实现 `ProviderRegistry`：
  - provider 由代码注册。
  - 未知 provider 返回明确错误（复用既有 `UnknownProvider`）。
  - TOML 不能注入任意 provider 类名。
- 实现 `FakeModelProvider`：
  - 支持固定文本回复。
  - 支持测试注入 usage。
  - 支持测试注入 provider 错误。
  - 不读取环境变量或真实配置文件。
- 实现 `LlmGateway.complete` 最小路径：
  - 接收 `ModelRequest`。
  - 根据已解析 provider/model 调用 provider。
  - 归一化 `ModelResponse`。
  - 确保 response 带 `request_id`、provider、model、finish reason、usage 或 estimated usage。
- provider capabilities 只描述 adapter 级能力，不把单个模型能力写进 provider。
- 为后续路由预留 catalog 校验入口，但今日可以用显式 provider/model 测试主路径。

## 非目标

- 不做 tier 路由。
- 不做 credential 解析（仅保留 availability 轻量判断）。
- 不做真实 provider HTTP 请求。
- 不写 session event 或 usage 文件。

## 最终产物

- 并轨后的统一 `ProviderRegistry`（含 adapter 绑定，覆盖 deepseek / mimo / openai / local）。
- `FakeModelProvider`。
- `LlmGateway.complete` happy path。
- gateway 单元测试和 fake provider 单元测试。

## 验收重点

- 测试 deterministic，不依赖网络、时间随机性或真实 API key。
- gateway 返回统一响应，不能把 provider 私有响应对象暴露给 application 上层。
- fake provider 可以覆盖成功、无 usage、provider error 三类测试场景。
- 不存在两套 provider 注册表或两套凭证概念；既有切片均已并轨或职责明确分离。

## 验收命令

```bash
make ci
poetry run pytest tests
```
