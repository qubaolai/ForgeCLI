# 2026-07-07：结构化输出、AgentTurn 集成与取消信号

## 今日目标

实现 `complete_structured` 的 schema 校验最小能力，让 `AgentTurnService` 通过 `LlmGateway` 获取 assistant message（不再生成 stub 回复），并把 `cancel_token` 从 AgentTurn 接线到 gateway 与 fake provider。

## 开发指导

- 实现 `complete_structured` MVP：
  - 接收 schema 和 schema_name。
  - provider 支持原生结构化输出时使用 provider 能力标记。
  - fake provider 可直接返回结构化数据。
  - 返回数据必须经过 schema 校验。
  - 校验失败归一化为 `ModelResponseParseError`。
  - 受控解析降级（provider 无原生 structured output 时注入 schema 指令 + 有上限重试）本阶段只在接口上预留，MVP 不实现，记入 backlog。
- 集成 Agent turn：
  - 普通 chat turn 构造 `ModelRequest`，`origin=chat` 默认使用 `current_model`。
  - 调用 `LlmGateway.complete`，assistant message 来自 `ModelResponse.content`。
  - usage 草稿（07-06）由 `AgentTurnService` 写入事件或保留在 session event payload 中——确立 `AgentTurnService` 为 usage 与事件写入边界。
  - `AgentTurnService` 在构造 `ModelRequest` 时注入 `budget_snapshot`（MVP 内 `BudgetGuard` no-op）。
- 取消信号接线：
  - `AgentTurnService` -> gateway -> provider 透传 `cancel_token`。
  - fake provider 支持协作式取消，gateway 产出 `interrupted=true` 收尾、`finish_reason=user_cancelled`，并按已接收内容估算 usage 标记 `estimated=true`。
  - 取消后不得留下未关闭的 turn；归一化为 `ModelCancelledError` 或带 interrupted 摘要的可恢复结果。
  - 真实 HTTP 在途中止在 07-08 的 adapter 上验证；本日先打通非流式协作式取消链路。
- 如果 `AgentLoop`（ADR-0010）尚未完整落地，可先实现最小 `BuiltinAgentLoop` 或 application service 适配，但必须保证上层只依赖 gateway 端口。把「AgentLoop 层暂以 AgentTurnService 直连 gateway」作为偏差记录写入当日验收材料。
- 失败路径要返回可理解 assistant 错误提示，并写入安全摘要。

## 非目标

- 不实现 streaming chunk。
- 不实现 tool calling。
- 不实现 context compact。
- 不实现真实 provider 网络调用（adapter 在 07-08）。

## 最终产物

- `complete_structured` 最小实现。
- schema 校验和 parse error 测试。
- `AgentTurnService` 通过 gateway 完成 chat turn 并写入 user/assistant/usage 事件。
- `cancel_token` 非流式取消链路与 interrupted 摘要测试。
- AgentLoop 层取舍的偏差记录。

## 验收重点

- `AgentTurnService` 是 usage 和事件写入边界。
- gateway 不 import session store；CLI 不 import provider adapter。
- 单轮 chat 可通过 fake provider 完成并写入 user/assistant 事件。
- 取消信号能中止协作式调用，返回 `interrupted=true` 收尾，不留未关闭 turn。

## 验收命令

```bash
make ci
printf 'hello\n' | poetry run forge
```

如果 TTY 限制导致管道无法完整驱动 REPL，应以 `AgentTurnService`、gateway 和 session service 的集成测试作为主验收。
