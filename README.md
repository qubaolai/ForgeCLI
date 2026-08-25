# ForgeCLI

ForgeCLI 是本地优先、由浏览器控制的对话式软件工程 Agent。Agent Runtime、文件、Git、
受控 Shell、沙箱、审批、恢复和 session 数据全部留在本机；浏览器只提供项目、会话和控制界面。

## 开发运行

```text
poetry install
cd web && npm ci && cd ..
poetry run forge
```

裸 `forge` 会在 `127.0.0.1:8765` 启动本地服务并打印一次性启动链接；默认不自动打开浏览器。
端口固定，重启 Forge 后已经打开的页面会自己连回来。需要自动打开或换端口时：

```text
poetry run forge --open --port 8899
```

## 排查问题

运行日志写在 `~/.forge/logs/`, 一次运行一个文件, `forge-latest.log` 指向本次运行:

```text
tail -f ~/.forge/logs/forge-latest.log
```

默认级别 `info` 记录一次运行的骨架 (启动, turn 起止, 模型调用, 工具裁决与执行结果);
`FORGE_LOG_LEVEL=debug` 额外写整段提示词, 模型回复原文, 工具输出与 HTTP payload.
终端里 `/diagnostics` 给出日志位置, 各阶段耗时分位数与模型调用读数.

日志**不脱敏** (凭证的值除外), 贴给别人之前请先自己看一眼. 详见
[docs/使用相关.md](docs/使用相关.md) 与 ADR-0035.

## 质量检查

```text
make ci
```

该命令检查 Python lint/format/mypy/架构依赖、React/TypeScript 类型与生产构建，并运行
pytest。会话与项目状态继续保存在用户级 `~/.forge/projects`，不污染工作区。
