# 2026-06-26：会话存储骨架与本周集成验收

## 今日目标

建立长任务最小持久化骨架，让交互输入、斜杠命令和状态可以写入本地 session 文件。

## 开发指导

- 创建 `.forge/sessions/<session_id>/events.jsonl` 和 `state.json` 的最小结构。
- 普通输入写入 `user_message` 事件。
- 斜杠命令写入 `slash_command` 或 `mode_changed` 事件。
- `state.json` 至少记录 session id、workspace root、mode、last_event_id、updated_at。
- `state.json` 写入使用临时文件 + 原子替换。
- `forge status` 读取当前 session 状态。
- 不实现 resume、compact、工具调用和 LLM 对话；只保留后续扩展点。

## 最终产物

- `EventStore` 最小实现。
- `StateStore` 最小实现。
- 交互式 REPL 与事件写入集成。
- `forge status` 展示 session 基础信息。
- 本周集成验收记录。

## 代码验收

我会检查 JSONL 追加、state 原子写入、事件类型区分、workspace root 绑定和本周全部命令的 `make ci` 结果。
