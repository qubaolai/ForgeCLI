# 2026-07-23：AgentLoop 边界冻结

## 今日目标

按 ADR-0010 §4/§6/§7 冻结 `AgentLoop` 的输入输出契约与扩展机制骨架，只定型字段与端口，不实现
循环体。让后续 `BuiltinAgentLoop`、hooks、events 有稳定的形状可依附。

## 开发指导

- 定义 `LoopInput`（turn_id / session_id / user_intent / mode / mode_policy / context_package /
  tool_catalog / budgets / resume_state）与 `LoopState`（step_index / observations / pending_actions
  / budgets / retry_state），字段一次冻全；`LoopState` 不保存 raw chain-of-thought。
- 定义每次迭代三选一的产出：`LoopDecision`（reason_summary / next_action / continue_reason）、
  `LoopAction`（answer / request_tool / ask_user / request_approval / request_compaction）、
  `LoopStop`（reason / message / resumable）。
- 冻结 `LoopStopReason` 全集（FINAL_ANSWER / WAIT_USER_INPUT / WAIT_APPROVAL / USER_CANCELLED /
  BUDGET_EXHAUSTED / POLICY_DENIED / CONTEXT_COMPACTION_REQUIRED / TOOL_FAILED_BLOCKING /
  MODEL_ERROR_BLOCKING / SESSION_INTERRUPTED / MAX_RETRY_EXCEEDED）与分类规则。
- 定义 `LoopHook`（before_step / before_model / after_model / before_action / after_action →
  `HookResult`）、`LoopEvent` 只读通知集合、最小 `LoopEventBus` 骨架。

## 非目标

- 不实现循环体、不接 gateway、不接工具。
- 不实现具体 hook / subscriber，只冻端口。

## 最终产物

- `application/agent_loop` 下的 loop DTO 与端口（`AgentLoop` abstract、`LoopInput/State/
  Decision/Action/Stop`、`LoopStopReason`、`LoopHook/HookResult/LoopEvent/LoopEventBus`）。
- 契约层单元测试（字段冻结、`LoopStopReason` 分类、三选一产出互斥）。

## 验收重点

- `AgentLoop` 端口只声明产出 `LoopDecision/LoopAction/LoopStop`，不含任何副作用方法。
- `application/agent_loop` 不 import CLI / filesystem / shell / git / 具体 LLM SDK。
- `LoopStopReason` 覆盖 ADR-0010 §6 全集，可恢复/阻塞分类有测试。

## 验收命令

```bash
make ci
```
