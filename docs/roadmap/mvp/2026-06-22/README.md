# 2026-06-22：CLI 应用壳与入口契约

## 今日目标

把 19 日的最小 `print` 入口升级为真实 CLI 应用壳，明确 `forge`、`forge --help`、`forge --version` 和基础子命令的行为边界。

## 开发指导

- 引入 CLI 层依赖：Typer 作为命令框架，Rich 作为终端输出基础。
- `forge --help` 输出真实帮助信息，不再只是打印 `forgecli`。
- `forge --version` 输出当前版本。
- 裸 `forge` 保留为交互式会话入口；今天可以先进入可退出的 REPL stub。
- 增加 `forge chat`、`forge status` 的最小命令占位，但不得接入 LLM 或工具系统。
- CLI 层只负责解析参数和渲染输出，不把业务逻辑写进命令函数。

## 最终产物

- Typer CLI app。
- `forge --help`、`forge --version`、`forge chat`、`forge status` 可运行。
- 裸 `forge` 能启动并安全退出 REPL stub。
- CLI smoke tests 覆盖 help、version 和基础命令。

## 代码验收

我会检查 CLI 命令是否稳定、输出是否可测试、是否没有把 Agent workflow、配置写入或工具执行提前塞进 CLI 层。
