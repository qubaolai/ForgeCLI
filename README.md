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

## 质量检查

```text
make ci
```

该命令检查 Python lint/format/mypy/架构依赖、React/TypeScript 类型与生产构建，并运行
pytest。会话与项目状态继续保存在用户级 `~/.forge/projects`，不污染工作区。
