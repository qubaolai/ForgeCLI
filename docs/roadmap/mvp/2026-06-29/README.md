# 2026-06-29：LLM Gateway 边界冻结

## 今日目标

根据 ADR-0011 定义统一 LLM 调用网关的 application 边界，先把端口、DTO、错误类型和依赖方向冻结下来，为后续 fake provider、路由、usage 计量和 AgentTurn 集成提供稳定基础。冻结日要一次冻全字段，避免后续预算、取消、流式接入时再改动「已冻结」DTO。

## 开发指导

- 在 `application/llm` 下建立 gateway 相关模块，优先复用现有 `application/llm` 配置切片，不提前创建空的远期模块。既有 `providers.py`、`availability.py`、`model_ref.py`、`errors.py` 的并轨收敛安排在 06-30，今日只需保证新 DTO 不与它们命名冲突。
- 定义 `LlmGateway` 端口：
  - `complete(request: ModelRequest) -> ModelResponse`
  - `complete_structured(request: StructuredModelRequest) -> StructuredModelResponse`
  - `stream(...)` 可先只保留接口或明确 deferred，不在今日实现。
- 定义 `ModelProvider` adapter 接口，只描述供应商协议映射，不读写 session、配置、事件或预算状态。
- 定义统一 DTO：
  - `ModelRequest`
  - `ModelResponse`
  - `ModelUsage`
  - `ModelParams`（含 `ThinkingConfig`）
  - `ModelSelection`
  - `StructuredModelRequest`
  - `StructuredModelResponse`
- 一次冻全 `ModelRequest` 字段，即使本阶段为 no-op 也要先占位，避免后续 DTO churn：
  - `request_id`、`session_id`、`turn_id`、`loop_step_id?`、`origin`、`model_selection`。
  - `required_capabilities[]`、`min_context_window?`、`messages[]`、`system_prompt?`、`tools[]`、`params`、`timeout_seconds?`。
  - `budget_snapshot?`（预算快照，MVP 内 `BudgetGuard` no-op，但字段位先冻）。
  - `cancel_token?`（取消信号，接线在 07-07，字段位先冻）。
  - `metadata`（只放安全摘要，不含 secret）。
- 定义完整 `ModelRequest.origin` 枚举，与 ADR-0011 §3.3 / §3.4 映射表一一对应：`chat`、`act`、`tool_observation`、`final_summary`、`title`、`summary`、`compact`、`plan`、`review`、`debug`、`structured_classification`。`tool_observation` 与 tool calling 一同推迟实现，但枚举值今日先冻。
- 定义统一错误类型：auth、rate limit、timeout、context overflow、bad request、provider internal、parse error、budget exceeded、cancelled。错误类型与 config 层的 `UnknownProvider`/`InvalidModelRef` 分层清晰，命名不混用。
- 明确边界：gateway 不直接写 `events.jsonl`、`state.json`、usage 文件或日志原文。

## 非目标

- 不接真实 provider SDK。
- 不实现模型路由和 fallback。
- 不实现 streaming、tool calling 或结构化输出校验。
- 不实现 `cancel_token` / `budget_snapshot` 的运行时行为（仅冻结字段位）。
- 不改 REPL 行为。

## 最终产物

- LLM gateway 核心端口和 DTO（含全量 `ModelRequest` 字段位与完整 origin 枚举）。
- provider adapter 接口。
- 统一错误类型。
- 单元测试覆盖 DTO 校验、selection 字段互斥、origin 枚举完整性和错误类型基本行为。

## 验收重点

- `application` 不 import 任何具体供应商 SDK。
- provider adapter 不接触 session store、state store、event store。
- 明文凭证字段不得出现在 DTO、测试 fixture 或异常字符串中。
- `current_model`、`tier`、`explicit_model` 的字段组合必须有清晰校验规则。
- `ModelRequest` 不带 `stream` 标志；流式与否由 `complete` / `stream` 决定。
- origin 枚举与 §3.4 映射表覆盖一致，新增用途必须先扩枚举。

## 验收命令

```bash
make ci
poetry run pytest tests
```
