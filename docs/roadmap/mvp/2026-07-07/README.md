# 2026-07-07：Slash Command 与模式切换

## 今日目标

实现 slash command 路由、chat/plan/act 模式切换，以及配置和模型命令的交互式入口。

## 开发指导

- 支持 `/help`、`/chat`、`/plan`、`/act`、`/config`、`/models`、`/status`、`/pause`、`/exit`。
- 模式切换写入 `mode_changed` 事件。
- `/config` 复用 `ConfigService`，与 `forge config ...` 行为一致。
- `/models` 复用 `ModelCatalogService`，与 `forge models ...` 行为一致。
- 斜杠命令写入事件日志，并与普通自然语言输入区分。
- 当前模式写入 state。

## 最终产物

- `IntentRouter`。
- slash command 测试。
- 模式切换测试。
- `/config` 和 `/models` 路由测试。
- 未知命令错误提示和 `/help` 测试。

## 代码验收

我会检查 slash command 是否与普通用户消息区分清晰，模式切换是否可恢复，控制命令是否不会被模型当作普通文本处理。
