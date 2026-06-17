# 2026-06-23：交互式输入与斜杠命令路由

## 今日目标

建立会话内输入解析能力，让自然语言、斜杠命令、模式切换和退出控制可以被稳定区分。

## 开发指导

- 定义 `IntentRouter` 的最小 application 接口。
- 支持普通自然语言输入识别为 `user_message` intent。
- 支持 `/help`、`/chat`、`/plan`、`/act`、`/status`、`/pause`、`/exit`。
- 未知斜杠命令必须给出可操作错误，并提示 `/help`。
- 模式切换今天只更新内存态，不接入 LLM 和工具执行。
- 保持 CLI REPL、IntentRouter、领域值对象之间的依赖方向清晰。

## 最终产物

- `IntentRouter` 最小实现。
- slash command intent 数据结构。
- REPL stub 复用 IntentRouter。
- 单元测试覆盖自然语言、已知斜杠命令、未知斜杠命令和退出命令。

## 代码验收

我会检查命令解析是否不依赖模型、是否可测试、是否没有把斜杠命令直接当作普通自然语言处理。
