# 2026-07-09：Streaming 与 Tool Calling 归一化

## 今日目标

在 07-08 provider 请求闭环之上，完成 ADR-0011 要求的 streaming、tool calling schema 转换和 tool result 回填归一化，使它们正式进入 MVP，而不是留在 backlog。

## 开发指导

- 增加统一 `ModelStreamChunk` / `ProviderStreamChunk` DTO 与 gateway、provider 的 `stream` 端口。
- 归一化 text delta、tool call delta、finish reason、usage delta 和 provider request id。
- stream 中断时返回已接收的 partial 内容、`interrupted=true`、安全摘要和 estimated usage。
- 将 ForgeCLI `ToolSpec` 转成 OpenAI tool schema，校验 name、description、parameters schema。
- 将 provider tool call 归一化为 tool name、arguments、tool_call_id，禁止未经 schema 校验的直接执行。
- 将 ToolResult 回填为统一 message，保证 tool_call_id 关联，并支持下一次 gateway 调用继续对话。
- 保持工具执行边界：gateway/adapter 只负责协议转换，不执行 shell、文件或外部工具；完整 ToolRuntime 和审批仍是后续 MVP 切片。
- 用 fake provider/transport 覆盖 streaming 合并、tool call、tool result 回填、解析失败和中断。

## 非目标

- 不实现完整 ToolRuntime、工具注册、审批和 Agent 主循环。
- 不实现 prompt/response cache、熔断、真实限流和完整预算扣减。
- 不在 stream 错误时自动切换模型。

## 最终产物

- gateway/provider streaming 端口和 chunk 归一化。
- tool schema、tool call、tool result 的统一 DTO 和 adapter 转换。
- partial/interrupted 处理。
- streaming、tool calling 和 tool result 回填测试。

## 验收重点

- `complete` 和 `stream` 使用同一 ModelRequest 语义，不增加 `stream` 字段。
- stream chunk 能合并成完整 response，tool call delta 不丢失或重复。
- tool call 与 tool result 通过 `tool_call_id` 关联。
- gateway 不执行工具、不直接落盘、不泄露 provider 原始 secret。

## 验收命令

```bash
make ci
poetry run pytest tests
```
