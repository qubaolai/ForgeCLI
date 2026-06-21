# 2026-06-24：交互式配置与 EffectiveConfig

## 今日目标

实现本地配置的最小闭环，让交互式终端中的 `/config` 能查看和修改运行时配置，并让未配置项目可靠使用默认值。

## 开发指导

- 定义 `ConfigService` 和不可变 `EffectiveConfig`。
- 不初始化 `.forge/config.toml`。当项目未配置时，`ConfigService` 应返回默认 `EffectiveConfig`。
- 只有用户在 `/config` 中首次修改配置时，才创建或更新 `.forge/config.toml`。
- `/config` 菜单只负责交互呈现和调用 service，不直接堆叠配置读取、写入、合并或校验逻辑。
- 配置业务边界放在 `application/config` ；它负责默认值、有效配置视图、配置键约束、敏感字段保护和更新用例。
- 文件系统 TOML 读写放入 `infrastructure` , 以便后续支持项目配置、用户配置、环境变量、远程配置或多来源合并。
- `.forge/config.toml` 中的未知字段直接忽略，不进入 `EffectiveConfig`，也不作为本日错误处理目标。
- `.forge/config.toml` 语法错误时必须返回用户可理解的配置读取错误，不能向 CLI 暴露 traceback。
- Python 读取 TOML 使用标准库能力；若需要写 TOML，明确引入最小依赖或封装写入器。

## 最终产物

- `application/config` 目录设计和最小实现。
- `ConfigService` 最小实现。
- `EffectiveConfig` 和配置值对象。
- 未配置时返回默认配置的能力。
- `/config` 交互式配置入口复用 `ConfigService`。
- service 单元测试和 `/config` 交互入口相关测试。

## 代码验收

我会检查默认配置是否可靠、配置更新是否复用 `ConfigService`、可配置项是否由应用封闭定义且不包含凭证字段、`/config` 与 service 的职责是否分离、未知字段是否被忽略、TOML 语法错误是否能被友好处理，以及配置文件写入是否可重复、可验证。

## 补充实现内容

本日实际实现中额外补充了 LLM 配置切片，作为 `/config` 交互式配置的一部分：

- 在 `/config` 根菜单中增加“供应商配置”和“模型配置”入口。
- 供应商集合由应用代码封闭定义，当前包含 `deepseek` 和 `mimo`；用户不能通过配置文件新增未实现供应商。
- 可配置供应商展示名、API base、API key 环境变量名、超时和重试次数。
- API key 环境变量名只是凭证读取位置的名称，不是凭证值；真实 API key、token、secret 不写入配置文件。
- 模型列表由配置驱动，支持在交互菜单中添加、删除模型，并编辑上下文窗口、最大输出 tokens、采样参数、价格元数据和厂商扩展 JSON。
- LLM 配置文件使用 `.forge/llm.toml`，文件读写实现放在 `infrastructure/llm/config`，application 只依赖 `LlmConfigStore` 端口。
- `.forge/llm.toml` 不预先创建，只有用户在 `/config` 中修改供应商或模型配置时才写出。
- 读取 `.forge/llm.toml` 时，未知供应商段直接忽略；TOML 语法错误返回用户可理解的配置读取错误。
- 本切片只覆盖供应商和模型配置，不接入真实 LLM 调用，不实现 provider adapter，不替代 2026-06-25 的模型目录和运行时模型选择验收。
