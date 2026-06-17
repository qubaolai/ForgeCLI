# 2026-06-29：ConfigService

## 今日目标

实现配置加载、合并和查询能力。

## 开发指导

- 支持默认配置、用户 `~/.forge/config.toml`、项目 `.forge/config.toml`、环境变量、CLI 参数。
- 企业只读策略作为安全上限处理。
- 提供不可变 `EffectiveConfig`，通过应用服务注入 session/turn。
- LLM key、token、secret 只从环境变量或系统凭证读取。

## 最终产物

- `ConfigService`。
- 配置优先级测试。
- 不可变 `EffectiveConfig` 测试。
- 配置校验错误测试。

## 代码验收

我会检查配置合并是否可预测，企业策略是否不能被用户降低，领域层是否没有直接读取全局配置或环境变量。
