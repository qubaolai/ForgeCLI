# ForgeCLI 验收标准

## 1. 目标

本标准用于每日开发成果、阶段里程碑和 PR 的代码验收。验收由我执行，开发者需要提供变更范围、对应日期计划、关键文件和已知风险。验收结论分为：

- 通过：满足当日目标，质量门禁通过，无阻塞风险。
- 暂不通过：存在阻塞缺陷、测试失败、架构偏离或验收项缺失。
- 条件通过：主路径可用，但存在明确的非阻塞遗留项，必须记录到下一日计划。

## 2. 验收输入

每次验收前需要确认：

- 对应日期文档，例如 `docs/roadmap/mvp/2026-06-22/README.md`。
- 本次代码变更范围和非目标。
- 关键实现文件和测试文件。
- 运行环境是否已执行 `make install`。
- 是否有偏离 ADR 或详细设计的实现。

## 3. 基础命令验收

默认执行：

```bash
make ci
make run
poetry run forge --help
poetry run forge --version
```

按功能追加执行：

```bash
printf 'exit\n' | poetry run forge
```

后续阶段按能力增加 `/config`、`/models`、`/status`、`/resume` 的 REPL 集成测试、工具调用、事件存储和 E2E 场景命令。当前 MVP 不验收 `forge chat/status/config/models/resume` 这类 Typer 业务子命令。

## 4. 代码结构验收

检查目录和依赖方向：

- 项目采用 `src/forgecli + tests`。
- CLI 层位于 `src/forgecli/interfaces/cli`。
- `domain` 不依赖 CLI、Rich、Typer、LLM SDK、MCP SDK、文件系统或 shell。
- `application` 负责编排用例，不承载终端渲染。
- `tools`、`infrastructure`、Agent framework adapter 只在对应阶段创建。
- MVP 不提前创建空的未来模块。
- console script 入口必须稳定，例如 `forgecli.interfaces.cli.app:main`。

## 5. 语法与静态分析

每次验收必须通过：

- `poetry check`
- `poetry run ruff check .`
- `poetry run ruff format --check .`
- `poetry run mypy`
- `poetry run pytest`

静态分析重点：

- import 顺序、未使用 import、格式化必须干净。
- mypy strict 下无类型错误。
- 依赖变更必须同步 `poetry.lock`。
- `pyproject.toml` 使用 PEP 621 `[project]` 元数据。
- 新增命令、配置项或依赖必须有测试覆盖。

## 6. 逻辑验收

检查实现是否满足当日文档：

- CLI 命令输出可预测、可测试、退出码正确。
- 裸 `forge` 的交互入口可安全退出，不在测试中阻塞 stdin。
- 斜杠命令不得直接交给模型自由解释。
- 配置命令必须复用 `ConfigService`，不得在 CLI 函数里直接堆业务逻辑。
- 模型目录由 `ModelCatalogService` 管理，`[model]` 只表示运行时默认模型；26 日起运行时默认模型属于项目级 `forge.toml`。
- session 写入必须区分自然语言、模式切换和系统事件；斜杠命令事件在 2026-06-27 切片暂不要求落盘。
- 文件写入、shell、网络、删除等副作用必须有权限边界和测试。

## 7. 测试验收

测试由我在验收时执行，并按失败信息给出阻塞项。测试要求：

- CLI 使用 `typer.testing.CliRunner` 或等价方式测试，不直接调用会进入 REPL 的 `main()`。
- REPL 必须测试 `exit`、`quit`、EOF/Ctrl-D 等退出路径。
- 配置和存储测试必须写入临时目录，不污染用户 HOME。
- 领域逻辑测试不得依赖真实外部服务。
- P0/P1 修复必须有回归测试。

## 8. 安全与副作用验收

检查：

- 不把 API key、token、secret 写入配置、日志、事件或测试 fixture。
- workspace 外写入必须拒绝或显式审批。
- destructive、network、external 行为必须分类并要求确认。
- 测试不得执行真实 push、发布、远程删除或真实模型调用。

## 9. 文档一致性验收

代码行为变化时必须同步：

- 当日 roadmap 文档。
- 相关 ADR。
- `docs/02-detailed-design.md` 中的模块边界或流程。
- `docs/README.md` 索引。

若实现与文档冲突，默认以已接受 ADR 和最新日期计划为准，并要求补充 ADR 或更新设计文档。

## 10. 验收报告格式

每次验收输出：

```md
结论：通过 / 暂不通过 / 条件通过

已验证：
- 命令和结果
- 关键功能路径

阻塞问题：
- 文件:行号 + 问题 + 修复建议

非阻塞建议：
- 后续优化项

下一步：
- 可进入的日期计划或必须先修复的事项
```
