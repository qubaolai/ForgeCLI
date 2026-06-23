# 2026-06-25：单入口收敛与配置单源模型选择

## 今日目标

基于当前代码实现进入 25 日开发，重点不是恢复历史占位命令，而是把 MVP 入口方向收敛清楚：

- 只保留裸 `forge` 进入交互式会话，Typer 只承担根入口、`--help` 和 `--version`。
- 删除并禁止恢复 `forge chat`、`forge status`、`forge models ...` 等 Typer 子命令。
- 继续沿用当前已经合理落地的目录结构和 `/config` 交互菜单。
- 供应商配置、模型声明和运行时默认模型都以配置文件为主要事实来源，避免模型目录、命令参数和配置文件多路径不一致。
- 在交互式会话内实现 `/model` 模型选择面板；不实现 `/model list/current/use` 这类参数式 slash command。

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
- `/config` 根菜单已经能进入基础配置、供应商配置和模型配置；供应商与模型声明最终落在配置文件中。
- LLM 配置当前封闭 provider 集合为 `deepseek` 和 `mimo`。
- 配置目录解析支持用户级 `~/.forge` 或 `FORGE_CONFIG_DIR`。

24 日额外实现的 LLM 配置切片是合理的阶段性新增，不需要回滚。25 日继续沿用该切片作为模型声明来源，不再额外引入模型目录服务。

## 已废弃方向

以下内容与当前系统方向不符，25 日不得继续实现：

- `forge chat` Typer 子命令。
- `forge status` Typer 子命令。
- `forge models list/current/use` Typer 子命令。
- `/model list`、`/model current`、`/model use ...` 等参数式 slash command。
- 独立于配置文件的 `ModelCatalogService`、内置 fallback model catalog 或远程 catalog。
- 为脚本入口复制一套和 slash command 平行的业务流程。

如果需要状态、模型、配置等能力，全部优先通过 REPL 内 slash command 暴露，并复用 application service。模型相关交互以菜单面板为主，不做第二套命令参数语义。

## 25 日必须开发

### 1. 固化单入口 CLI

- 确认 `interfaces/cli/app.py` 只保留根回调、`--help`、`--version` 和 `main()`。
- CLI smoke tests 必须断言 `chat`、`status` 不是已注册 Typer 子命令。
- 裸 `forge` 继续进入 REPL 并能通过 `exit` 或 EOF 安全退出。

### 2. 固化配置单源边界

- `application/llm/config` 继续负责供应商与模型声明，包括 provider 元数据、模型 id 和模型参数。
- `infrastructure/llm/config` 继续负责 `.forge/llm.toml` 的 TOML 读写。
- 运行时默认模型只保存稳定引用，不复制 provider 或模型参数。
- `application/config` 负责 `.forge/config.toml` 中 `[model]` 默认模型引用的读取和写入。
- 不创建 `application/models`、`ModelCatalogService`、内置模型目录或其他模型来源。
- Provider registry 只作为“支持哪些 provider”的封闭 schema、面板展示行和默认写入种子；模型列表必须来自配置文件中的有效配置。

### 3. 实现交互式 `/model` 面板

- 新增或保留 `/model` slash command，用于打开运行时默认模型选择面板。
- `/model` 不接受参数；传入参数时给出提示，要求直接输入 `/model` 打开面板。
- 面板保持现有内容结构：按 provider registry 展示受支持供应商行，并根据供应商 API key 环境变量给出可用性提示。
- 供应商下的模型列表只来自 LLM 配置；供应商没有模型时给出“去 `/config` 添加模型”的提示，不从内置目录补齐。
- 选择模型后调用 `ConfigService` 写入运行时默认模型，不允许 CLI handler 直接操作 TOML。
- 当前默认模型已经不在 LLM 配置中时，面板给出重新选择提示。

### 4. 接入运行时默认模型配置

- 在 `EffectiveConfig` 或等价配置视图中增加当前默认模型。
- 持久化结构使用 `[model]`，只保存 `provider` 和 `name` 或 `model` 这类稳定 id 字段。
- `ConfigService` 负责写入和读取；默认模型写入应通过一个明确用例一次性保存 provider 和 model，避免分两次写入造成中间态。
- 配置读取失败时沿用现有友好错误边界，不向用户暴露 traceback。

### 5. 补齐测试

- `/model` 面板测试：保持 provider 行展示，模型列表只使用配置文件中的模型，不依赖内置目录。
- `/model` 参数拒绝测试：`/model use`、`/model list` 等不进入面板。
- 默认模型写入和读取测试。
- CLI app 测试继续覆盖无 Typer 子命令、版本、help、裸 `forge` 退出。
- 所有配置写入测试必须隔离真实 HOME，优先使用 `FORGE_CONFIG_DIR` 或 fake store。

## 非目标

- 不接真实 LLM 调用。
- 不实现 provider adapter。
- 不实现远程 model catalog、refresh 或 recommend。
- 不实现内置 fallback model catalog。
- 不实现 session event 写入。
- 不实现完整项目/用户/环境变量配置合并。
- 不增加任何新的 Typer 业务子命令。
- 不增加 `/model list/current/use` 这类参数式 slash command。

## 最终产物

- 单入口 CLI 语义稳定。
- 配置文件作为供应商配置、模型声明和运行时默认模型的主要事实来源。
- `/model` 模型选择面板可用。
- 运行时默认模型可读取、校验和持久化。
- `/config` 负责供应商与模型声明，`/model` 负责从已声明模型中选择运行时默认模型。

## 验收命令

```bash
make ci
poetry run forge --help
poetry run forge --version
printf 'exit\n' | poetry run forge
```

`/model` 的行为以单元测试和 REPL 集成测试作为主验收；如果本地 TTY 能稳定驱动交互，可补充手工验证：输入 `/config` 添加供应商和模型，再输入 `/model` 选择默认模型。
