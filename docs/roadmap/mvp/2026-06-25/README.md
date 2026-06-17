# 2026-06-25：模型目录与运行时模型选择

## 今日目标

实现模型查看和运行时模型选择闭环，让用户可以通过 `forge models ...` 管理当前模型选择。

## 开发指导

- 实现 `ModelCatalogService` 的 MVP 版本。
- 使用内置模型目录作为离线 fallback，不做动态 provider registry。
- 支持 `forge models list`、`current`、`use <provider>:<model>`。
- `models use` 更新 `[model]` 运行时默认模型配置。
- 校验 provider 必须是代码中已经实现或显式声明支持的 provider。
- 模型能力元数据属于 ModelCatalogService，不写入 `[model]` 配置块。

## 最终产物

- 内置模型目录。
- `ModelCatalogService` 最小实现。
- `forge models list/current/use`。
- 与 `ConfigService` 的集成测试。

## 代码验收

我会重点检查 provider 边界是否清楚、模型目录是否与配置块解耦、`models use` 是否复用配置服务而不是绕过写入规则。
