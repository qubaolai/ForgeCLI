# 2026-06-24：配置命令与 EffectiveConfig

## 今日目标

实现本地配置的最小闭环，让 `forge config ...` 能创建、查看、校验和修改运行时配置。

## 开发指导

- 使用 `.forge/config.toml` 作为项目配置路径。
- 定义 `ConfigService` 和不可变 `EffectiveConfig`。
- 支持 `forge config init`、`list`、`get`、`set`、`validate`。
- `[model]` 表示运行时默认模型选择，不保存 API key、token 等敏感凭证。
- 配置读取和写入逻辑不要放在 CLI 命令函数中。
- Python 读取 TOML 使用标准库能力；若需要写 TOML，明确引入最小依赖或封装写入器。

## 最终产物

- `ConfigService` 最小实现。
- `EffectiveConfig` 和配置值对象。
- `.forge/config.toml` 初始化模板。
- CLI 配置命令测试和 service 单元测试。

## 代码验收

我会检查配置合并边界、敏感凭证保护、CLI 与 service 的职责分离，以及配置文件写入是否可重复、可验证。
