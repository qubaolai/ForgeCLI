# 2026-06-19：项目骨架设计验收

## 今日目标

在写代码前确认包结构、依赖方向、测试策略和首个迭代入口。

## 开发指导

- 根据详细设计确认 `interfaces`、`application`、`domain`、`tools`、`infrastructure`、`shared` 目录边界。
- 确认 Python 3.13+、Poetry、pytest、Ruff、mypy。
- 确认采用 `src/forgecli + tests` 布局。
- 确认 MVP 骨架仅创建当前阶段需要的文件，不提前创建未来阶段空目录。
- 确认 CI 本地等价命令。

## 最终产物

- 项目结构清单。
- 工程工具选型记录。
- 初始骨架最小目录清单。
- 第一周开发任务拆分。

## 已完成项

基于当前实现，19 日工程骨架已完成以下内容：

- 建立 `pyproject.toml`，采用 PEP 621 的 `[project]` 元数据。
- 明确 Python 版本约束为 `>=3.13,<4.0`。
- 使用 Poetry 管理依赖，并生成 `poetry.lock`。
- 建立 `src/forgecli + tests` 项目布局。
- 建立最小包结构：`interfaces`、`application`、`domain`、`shared`。
- CLI 入口统一为 `forgecli.interfaces.cli.app:main`。
- 增加 `Makefile`，统一 `install`、`lock`、`check`、`lint`、`format`、`format-check`、`type`、`test`、`ci`、`run`。
- 增加 CLI smoke test，断言 `main()` 的标准输出和错误输出。
- 未引入 Agent 框架、MCP、工具系统、AutoGen adapter 等未来阶段能力。

## 验收结果

已通过以下命令：

```bash
make ci
make run
poetry run pytest
poetry run ruff check .
poetry run mypy src/forgecli
poetry run forge --help
```

当前 `forge --help` 可正常退出，但仍只是最小入口输出。真实 CLI help、version、子命令和交互式入口移入 2026-06-22 开发。

## 代码验收

今天可以提交工程骨架草案，但不要求业务逻辑。验收重点是目录边界清晰、依赖方向符合设计。

结论：19 日工程骨架验收通过，可以进入 2026-06-22 的 CLI 应用壳开发。
