# 2026-07-07：结构化输出、AgentTurn 集成与取消信号

## 今日目标

实现 `complete_structured` 的 schema 校验最小能力，让 `AgentTurnService` 通过 `LlmGateway` 获取 assistant message，并把 `cancel_token` 接到 gateway 与 fake provider。

## 开发指导

- 实现 `complete_structured`：将 schema、schema_name 和 strict 参数映射到 provider request，校验返回结构，失败归一化为 parse error。
- title、summary、plan update、risk classification 等内部调用默认使用当前模型，只有显式用途覆盖时使用覆盖模型。
- 将普通 chat turn 接入 gateway：构造 ModelRequest、保留 origin/mode 安全摘要、接收 ModelResponse。
- 由 `AgentTurnService` 写入 user、assistant 和 usage 摘要事件，gateway 不直接落盘。
- 将 `budget_snapshot` 以只读快照注入请求，MVP 内 `BudgetGuard` 不扣减预算。
- fake provider 支持协作式取消；gateway 返回 `interrupted=true`、`finish_reason=user_cancelled` 和必要的 estimated usage。
- 若 `AgentLoop` 尚未完整落地，记录 `AgentTurnService` 直连 gateway 的阶段偏差，不绕过 gateway。

## 非目标

- 不实现 streaming chunk、provider HTTP、tool call 执行和缓存。
- 不实现完整 AgentLoop、ToolRuntime、审批或上下文 compact。

## 最终产物

- `complete_structured` 和 schema parse error。
- `AgentTurnService` 的 gateway chat turn 集成。
- usage/event 写入边界和取消链路。
- AgentTurn、structured response 和取消测试。

## 验收重点

- 单轮 chat 通过 fake provider 完成并写入 user/assistant/usage 事件。
- structured response 严格校验 schema，失败不伪造成功结果。
- 取消能中止协作式调用，返回 interrupted 收尾，不留未关闭 turn。
- AgentTurn 不 import 具体 provider SDK。

## 验收命令

```bash
make ci
poetry run pytest tests
```
