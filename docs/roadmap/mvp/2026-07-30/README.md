# 2026-07-30：ToolRuntime + 只读工具 + 工具请求闭环

## 今日目标

按 ADR-0004 与 ADR-0010 §13(6-7) 落地 `ToolRuntime` / `ToolRegistry` 与只读工具
（read_file / glob / grep），并开放 `AgentLoop` 的 tool request 输出：loop 产出
`LoopAction.request_tool` → `ActionDispatcher` 经裁决执行 → observation 回填 loop。

## 开发指导

- `ToolRegistry`：代码级注册内置工具，每个工具带 `ToolSpec`（name / description / JSON schema）。
- `ToolRuntime` / `ActionDispatcher`：执行 `LoopAction.request_tool`，工具结果归一化为
  `LoopObservation` 回填；工具失败转 observation，不破坏 session。
- 只读工具 read_file / glob / grep 进只读 allow 集，经能力门进 `tool_catalog`（所有模式可见）。
- `BuiltinAgentLoop` 开放 tool request：answer / request_tool 两类出口 + observe 回到 reason。
- 工具事件按 append-only 落盘（tool_requested / tool_completed，带 invocation_id）；长 stdout 落
  artifact、事件只存摘要与路径（ADR-0010 §10）。

## 非目标

- 不接写/编辑工具（留 07-31）与 shell 工具（留 08-03）。
- 不接沙箱；只读工具本就无副作用。

## 最终产物

- `tools/` 下 `ToolRegistry` / `ToolRuntime` / `ActionDispatcher` + read_file / glob / grep。
- `BuiltinAgentLoop` 的 tool request → observe 闭环。
- 工具请求-执行-回填的集成测试（fake provider 驱动 loop 请求只读工具）。

## 验收重点

- Agent 可经 loop 读文件、glob、grep；工具结果回填后 loop 能继续或收尾。
- 工具请求必须是 `ToolRequest`，不能携带可执行匿名代码块绕过 registry。
- 工具事件带 turn_id / invocation_id；副作用只经 `AgentTurnService` 落盘。
- `AgentLoop` 只产出 tool request，不自己执行工具。

## 验收命令

```bash
make ci
```
