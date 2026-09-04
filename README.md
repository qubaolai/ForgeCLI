# ForgeCLI

ForgeCLI 是本地优先、由浏览器控制的对话式软件工程 Agent。Agent Runtime、文件、Git、
受控 Shell、沙箱、审批、恢复和 session 数据全部留在本机；浏览器只提供项目、会话和控制界面。

## 开发运行

```text
poetry install
make web-install
make web-build
poetry run forge
```

裸 `forge` 会在 `127.0.0.1:8765` 启动本地服务并打印一次性启动链接；默认不自动打开浏览器。
端口固定，重启 Forge 后已经打开的页面会自己连回来。需要自动打开或换端口时：

```text
poetry run forge --open --port 8899
```

## 终端会话

开不了浏览器 (SSH, tmux) 或者只想顺手问一句时，用终端入口：

```text
poetry run forge --cli
```

`make run-cli`是同一条命令的快捷方式。它需要一个真正的终端：stdin/stdout 被重定向时
forge 会直接拒绝启动 (退出码 4)，而不是挂在那里等一个永远不会来的回答。

它和 Web 是同一个 Forge：同一份装配、同一把项目锁、同一套裁决与审批语义，只是交互在终端里。
直接输入文字就是和模型说话。

- 输入 `/` 弹出命令菜单（命令名 + 说明），`↑↓` 选，回车确认，Esc 关闭；`/help` 列出全部。
  这些命令与 Web 上的面板一一对应：`/mode`、`/model`、`/config`、`/dirs`、`/plan`、
  `/checkpoints` 等。
- 菜单没打开时 `Tab` / `Shift+Tab` 沿权限梯度切换模式，当前模式显示在输入框右下角。
- 各级菜单和审批卡片都能用 `↑↓` 选、数字键直选。
- `Ctrl+J` 换行，`↑` 翻历史，`Ctrl-C` 停止当前一轮或取消当前这一步，空行连按两次退出。

同一个项目同时只能有一个 Forge 进程，所以 Web 和 `--cli` 不能同时开着同一个项目
(后启动的一方会告诉你占用者的 pid)。详见 [ADR-0045](docs/adr/2026-09-03-0045-恢复终端交互式入口并与Web控制面共存.md)。

## 排查问题

运行日志写在 `~/.forge/logs/`, 一次运行一个文件, `forge-latest.log` 指向本次运行:

```text
tail -f ~/.forge/logs/forge-latest.log
```

默认级别 `info` 记录一次运行的骨架 (启动, turn 起止, 模型调用, 工具裁决与执行结果);
把配置项"日志级别"(`logging.level`) 调成 `debug` 会额外写整段提示词, 模型回复原文,
工具输出与 HTTP payload —— 在控制面设置页的"常规配置"里改, 下次启动生效.
`GET /api/v1/diagnostics` 给出日志位置, 各阶段耗时分位数与模型调用读数.

日志**不脱敏** (凭证的值除外), 贴给别人之前请先自己看一眼. 详见
[docs/使用相关.md](docs/使用相关.md) 与 ADR-0035.

## 质量检查

```text
make ci
```

该命令检查 Python lint/format/mypy/架构依赖、React/TypeScript 类型与生产构建，并运行
pytest。会话与项目状态继续保存在用户级 `~/.forge/projects`，不污染工作区。
