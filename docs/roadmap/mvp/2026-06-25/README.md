# 2026-06-25：单入口收敛与交互式模型选择

## 今日目标

基于当前代码实现进入 25 日开发，重点不是恢复历史占位命令，而是把 MVP 入口方向收敛清楚：

- 只保留裸 `forge` 进入交互式会话，Typer 只承担根入口、`--help` 和 `--version`。
- 删除并禁止恢复 `forge chat`、`forge status`、`forge models ...` 等 Typer 子命令。
- 继续沿用当前已经合理落地的目录结构和 `/config` 交互菜单。
- 在交互式会话内实现 `/models` 模型目录和运行时默认模型选择。

## 已实现内容

当前代码已经具备并应继续保留：

- `src/forgecli` 与 `tests` 项目布局。
- 裸 `forge` 进入可退出 REPL。
- `forge --help` 和 `forge --version` 可用。
- Typer 层不再注册 `chat`、`status` 等子命令。
- `IntentRouter` 与 slash command registry。
- `/help`、`/chat`、`/plan`、`/act`、`/status`、`/config`、`/pause`、`/exit` 的交互式命令骨架。
- CLI 交互代码位于 `interfaces/cli`。
- slash command 基础设施位于 `application/slash_commands`。
- session 状态对象位于 `application/session`。
- 配置业务位于 `application/config`，TOML 适配器位于 `infrastructure/config`。
- LLM 配置值对象和 service 位于 `application/llm/config`。
- LLM 配置 TOML 适配器位于 `infrastructure/llm/config`。
- `/config` 根菜单已经能进入基础配置、供应商配置和模型配置。
- LLM 配置当前封闭 provider 集合为 `deepseek` 和 `mimo`。
- 配置目录解析支持用户级 `~/.forge` 或 `FORGE_CONFIG_DIR`。

24 日额外实现的 LLM 配置切片是合理的阶段性新增，不需要回滚；但它不是模型目录服务，25 日应在其基础上补齐运行时模型选择边界。

## 已废弃方向

以下内容与当前系统方向不符，25 日不得继续实现：

- `forge chat` Typer 子命令。
- `forge status` Typer 子命令。
- `forge models list/current/use` Typer 子命令。
- 为脚本入口复制一套和 slash command 平行的业务流程。

如果需要状态、模型、配置等能力，全部优先通过 REPL 内 slash command 暴露，并复用 application service。

## 25 日必须开发

### 1. 固化单入口 CLI

- 确认 `interfaces/cli/app.py` 只保留根回调、`--help`、`--version` 和 `main()`。
- CLI smoke tests 必须断言 `chat`、`status` 不是已注册 Typer 子命令。
- 裸 `forge` 继续进入 REPL 并能通过 `exit` 或 EOF 安全退出。

### 2. 实现 ModelCatalogService MVP

- 新增 `application/models`。
- 定义模型目录值对象，例如 provider id、model id、显示名、能力标签和默认候选。
- 新增 `ModelCatalogService`，负责列出可用模型、查询模型和校验模型引用。
- 内置 fallback catalog 至少覆盖当前计划可选模型，例如 `deepseek:deepseek-chat`。
- 不把用户在 `/config` 中声明的自定义模型参数当作模型目录本身；二者职责分开。

### 3. 实现交互式 `/models`

- 新增 `/models list`。
- 新增 `/models current`。
- 新增 `/models use <provider>:<model>`。
- `/models use` 必须通过 `ModelCatalogService` 校验后，再调用配置服务写入运行时默认模型。
- 未配置当前模型时，`/models current` 应给出可操作提示。
- 不增加 `forge models ...` Typer 子命令。

### 4. 接入运行时默认模型配置

- 在 `EffectiveConfig` 或等价配置视图中增加当前默认模型。
- 持久化结构使用 `[model]`，只保存 `provider` 和 `name` 或 `model` 这类稳定 id 字段。
- `ConfigService` 负责写入和读取，不允许 CLI handler 直接操作 TOML。
- 配置读取失败时沿用现有友好错误边界，不向用户暴露 traceback。

### 5. 补齐测试

- `ModelCatalogService` 单元测试。
- `/models list/current/use` handler 测试。
- 默认模型写入和读取测试。
- CLI app 测试继续覆盖无 Typer 子命令、版本、help、裸 `forge` 退出。
- 所有配置写入测试必须隔离真实 HOME，优先使用 `FORGE_CONFIG_DIR` 或 fake store。

## 非目标

- 不接真实 LLM 调用。
- 不实现 provider adapter。
- 不实现远程 model catalog、refresh 或 recommend。
- 不实现 session event 写入。
- 不实现完整项目/用户/环境变量配置合并。
- 不增加任何新的 Typer 业务子命令。

## 最终产物

- 单入口 CLI 语义稳定。
- `application/models` MVP。
- `/models list/current/use` 可用。
- 运行时默认模型可读取、校验和持久化。
- 配置、模型目录和 slash command 的边界清晰。

## 验收命令

```bash
make ci
poetry run forge --help
poetry run forge --version
printf 'exit\n' | poetry run forge
```

`/models` 的行为以单元测试和 REPL 集成测试作为主验收；如果本地 TTY 能稳定驱动交互，可补充手工验证：

```bash
printf '/models list\n/models current\n/models use deepseek:deepseek-chat\n/models current\nexit\n' | poetry run forge
```
