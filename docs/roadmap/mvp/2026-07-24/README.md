# 2026-07-24：BuiltinAgentLoop 最小实现 + AgentTurnService 接入

## 今日目标

按 ADR-0010 §5/§13(1-5) 落地只走一步的 `BuiltinAgentLoop`（answer + `FINAL_ANSWER`），让
`AgentTurnService` 改调 `AgentLoop` 替换 `GatewayReplier`，并接上 streaming 渲染与 Ctrl-C 取消。
本日把「循环骨架 + 事件流」在无工具的前提下真实跑通。

## 开发指导

- `BuiltinAgentLoop`：一次模型调用 → 产出 `LoopAction.answer` → `LoopStop(FINAL_ANSWER)`。
  经 `LlmGateway` 取回复，usage 草稿随回复交回，由 `AgentTurnService` 落盘（gateway 不直接落盘）。
- `AgentTurnService` 由直连 gateway（`GatewayReplier`）改为驱动 `AgentLoop`；成对写
  user_message / assistant_message，turn 终态写在 assistant 上，失败隔离为 FAILED。
- 接 streaming：`gateway.stream()` 进 REPL，Rich 增量渲染（此前 stream 代码零真实覆盖）。
- 接取消：REPL 的 Ctrl-C 挂真实 `CancelToken`（此前 `cancel_token_factory` 恒返回 None）。
- fake provider deterministic 测试：单轮 chat 经 loop 完成并写 user/assistant 事件。

## 非目标

- 不开放 tool request（留 07-30）；不接 ToolRuntime。
- 不实现 hook / event subscriber 的真实逻辑，只保证 bus 能发能收。

## 最终产物

- `BuiltinAgentLoop` 与 `AgentTurnService` 的 loop 接入；`GatewayReplier` 退出主路径。
- REPL streaming 增量渲染与 Ctrl-C 取消接线。
- 单轮 chat 的 loop 集成测试（fake provider）。

## 验收重点

- 所有模型调用仍经 `LlmGateway`；`AgentLoop` 不 import 具体供应商 SDK。
- 单轮 chat 经 loop 完成，user/assistant 成对落盘，usage 事件由 `AgentTurnService` 写入。
- streaming 有 deterministic 测试；Ctrl-C 能中止在途模型调用。

## 验收命令

```bash
make ci
poetry run forge --help
```
