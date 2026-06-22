# 2026-06-29：AgentTurnService Stub 与 REPL 解耦

## 今日目标

把 REPL 中直接处理自然语言输入的逻辑移入 `AgentTurnService` stub，让 CLI 只负责输入、输出和分派，为后续接入 workflow、LLM 和工具系统留出稳定边界。

## 当前基线

前一工作日应已具备：

- session 事件写入。
- `state.json` 快照。
- `/status` 读取 session state。
- 自然语言和 slash command 已能区分记录。

## 开发指导

- 新增 `application/agent_turn` 或 `application/conversation` 模块。
- 定义 `AgentTurnService.handle_user_message(...)`。
- REPL 的 `UserMessage` 分支只调用 service 并渲染返回结果。
- service 当前返回固定 assistant stub，但要写入 `assistant_message` 事件。
- mode 从 session state 读取，不再只依赖 REPL 内存态。
- plan/act/chat 模式切换通过 session service 更新 state。
- 不接真实 LLM，不引入 AgentWorkflow 具体实现。

## 最终产物

- `AgentTurnService` stub。
- `AssistantResponse` 或等价返回 DTO。
- REPL 与自然语言处理解耦。
- `user_message` / `assistant_message` 事件成对落盘。
- 单元测试覆盖 chat/plan/act 模式下的 stub turn。

## 验收重点

- CLI 层不再直接 sleep 或拼接 assistant 文本。
- application service 不依赖 Rich、Typer、prompt_toolkit。
- 事件顺序稳定：user_message -> assistant_message -> state update。
