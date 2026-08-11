# 2026-07-30：冻结 Shell 安全调用边界与 plan mode 能力门

## 今日目标

按 ADR-0013 冻结 Agent Shell 安全执行的应用层边界：`plan` mode 只向 LLM 暴露 `PlanTool` 和只读工具，
Shell 请求必须先进入通用工具授权入口，`ShellTool` 只负责执行已授权请求。

## 今日范围

- 定义 `PlanTool` 的最小接口：创建、更新和记录计划，不执行外部副作用。
- 定义 `ToolCatalog` 的 mode 过滤结果，至少覆盖 `plan`、`accept_edits`、`auto`、`full_access`。
- 定义 `ToolRequestCoordinator` 和通用 `ToolAuthorizationService` 的调用契约；Shell 请求通过
  `EXECUTE_SHELL` 能力分派给 `ShellCapabilityAnalyzer`。
- 定义 `ShellTool` 的薄执行接口和结构化执行结果。
- 定义 `tool_unavailable_in_mode`、`policy_denied` 和 `tool_result` observation。
- 定义 `ASK` 经人类批准后的全量绑定重验；批准本身不能直接作为执行授权。
- 保证 AgentLoop、slash command、resume 等入口不能绕过统一协调器。

## 非目标

- 不重构通用 Tool Registry、MCP 或所有内置工具。
- 不实现 Shell Parser、规则匹配和沙箱 Provider；这些在后续日期完成。

## 最终产物

- `PlanTool` 和只读工具的能力目录测试。
- `ToolRequestCoordinator` / `ToolAuthorizationService` 的接口和 fake 实现。
- `ShellTool` 只执行持有有效授权信封请求的边界测试。
- 审批后计划、目标、策略或执行环境变化导致旧批准失效的测试。
- plan mode 下 ShellTool 不进入模型工具目录；绕过请求返回结构化错误。

## 验收标准

- plan mode 工具目录只包含 PlanTool 和只读工具。
- ShellTool 内没有安全裁决和 LLM 调用逻辑。
- Shell 请求在调用 ShellTool 前经过统一协调器。
- 人类批准后完成计划、目标、策略和执行环境重验，再签发一次性执行授权。
- 安全拒绝能以 observation 回填给 LLM。
- 运行相关单元测试并执行 `make ci`。

## 关联决策

- ADR-0013 §1–§2。
