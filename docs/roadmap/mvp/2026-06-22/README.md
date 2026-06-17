# 2026-06-22：Python 项目初始化

## 今日目标

建立可运行、可测试、可安装的 Python CLI 项目骨架。

## 开发指导

- 创建 `pyproject.toml`。
- 使用 Poetry 初始化依赖管理，并生成 `poetry.lock`。
- 初始化 `src/forgecli + tests` 最小包结构。
- 仅创建当前阶段需要的模块，例如 CLI 入口、`shared` 基础模块和 smoke test。
- 配置测试、lint、format、typecheck。
- 提供最小 CLI 入口，能输出版本或帮助信息。

## 最终产物

- 可本地安装的项目骨架。
- `forge --help` 可运行。
- `poetry run pytest`、`poetry run ruff check .`、`poetry run mypy src/forgecli` 可执行。

## 代码验收

我会检查包结构、依赖方向、CLI 入口、Poetry 命令和是否没有引入不必要的 Agent 框架依赖或未来阶段空 adapter。
