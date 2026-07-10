# 2026-07-02：ModelSelectionResolver、用途覆盖与能力校验

## 今日目标

实现 07-01 冻结的 `ModelSelectionResolver`，把当前模型和按 `origin` 的显式模型覆盖解析为具体 provider/model，并在调用前完成 catalog 能力校验。

## 开发指导

- 默认所有 origin 使用 `[model].provider` / `[model].model` 当前模型。
- 支持可选的 `[model_overrides.<origin>]` provider/model 覆盖；未配置覆盖时不得生成第二套默认模型。
- 校验 provider 已注册、模型存在、allowlist 允许、context window 和请求所需 structured output、tool calling、thinking 能力满足要求。
- 能力不足、模型不可用或配置缺失时返回明确归一化错误；不得自动切换 provider/model。
- 将 resolver 接入 `DefaultLlmGateway`，使 `complete` 不再只接受显式 selection。
- 保留 `ModelRef` 作为 provider/model 的统一值对象，不新建平行引用类型。
- 为后续 TokenEstimator 预留 `required_capabilities` 和 `min_context_window`，但不在今日实现 token 估算。

## 非目标

- 不实现真实 provider、CredentialResolver、credential retry 或 provider health。
- 不实现 streaming、tool calling 执行、结构化输出解析和缓存。
- 不实现 `/config` 菜单交互，只完成 resolver 可消费的配置模型。

## 最终产物

- `ModelSelectionResolver` 运行时实现。
- 当前模型和用途覆盖的读取与校验。
- gateway 的 current/explicit 两类选择路径。
- 单元测试覆盖默认解析、用途覆盖、未知 provider、未知模型、allowlist 拒绝和能力不足。

## 验收重点

- 未配置用途覆盖时，所有 origin 都解析到当前模型。
- 当前模型或显式覆盖模型失败时直接返回错误，不改选其他模型。
- resolver 只读 catalog，不直接解析 TOML 或读取 provider 私有配置。
- `ModelSelectionResolver` 不负责凭证选择、usage 计量和重试。

## 验收命令

```bash
make ci
poetry run pytest tests
```
