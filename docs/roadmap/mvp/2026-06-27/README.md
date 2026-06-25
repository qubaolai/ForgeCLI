# 2026-06-27：会话事件存储骨架与状态快照

## 今日目标

在不接真实 LLM、不接工具系统的前提下，让交互输入和当前模式写入本地 session 文件，形成可恢复会话的最小存储基础。斜杠命令事件类型先保留在模型中，本日暂不落盘记录。

## 当前基线

前一日应已具备：

- 单入口 CLI 基线通过。
- `/models` 的交互式最小闭环。
- `SessionState` 已从 slash command 基础类型中拆出。
- CLI 菜单和命令位于 `interfaces/cli`。

## 开发指导

- 新增 `application/session` 下的 session 用例和端口。
- 新增 `infrastructure/storage` 或 `infrastructure/session` 下的 JSONL/state 文件适配器。
- 在用户级 Forge home 的 `projects/<project-id>/sessions/<session_id>/` 下创建 `events.jsonl` 和 `state.json`，不写入项目根目录。
- 普通输入写入 `user_message` 事件。
- `/chat`、`/plan`、`/act` 写入 `mode_changed` 事件。
- `/status`、`/config`、`/model` 等斜杠命令暂不写入 `slash_command` 事件；后续在命令返回明确写入结果后再接入。
- `state.json` 至少记录 session id、workspace root、mode、last_event_id、updated_at。
- `state.json` 写入使用临时文件 + 原子替换。
- 裸 `forge` 进入 REPL 时创建或绑定当前 session。
- `/status` 读取当前 session state，而不是只打印固定占位文案。

## 设计取舍

- 今天只实现当前 workspace 的 active session。
- 不实现历史 session 选择。
- 不实现 resume。
- 不实现 compact。
- 不把配置修改事件强制接入完整审计；`slash_command` 事件本日暂不落盘。

## 最终产物

- `SessionService` MVP。
- `EventStore` MVP。
- `StateStore` MVP。
- REPL 与事件写入集成。
- `/status` 展示 session id、workspace root、mode、last_event_id。

## 验收命令

```bash
make ci
printf '/status\n/plan\nexit\n' | env FORGE_CONFIG_DIR="$(mktemp -d)" poetry run forge
```

如果 TTY 限制导致管道无法完整驱动 REPL，应以单元测试和 store 集成测试作为主验收。
