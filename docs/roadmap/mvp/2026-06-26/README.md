# 2026-06-26：ToolSpec 与配置模型

## 今日目标

实现工具协议和配置领域模型，为后续 Tool Runtime 和 ConfigService 做准备。

## 开发指导

- 实现 `ToolSpec`、`ToolInvocation`、`ToolResult`。
- 实现 `ForgeConfig`、`ModelConfig`、`PolicyConfig`、`ToolConfig`、`ContextConfig`。
- 定义配置默认值。

## 最终产物

- Tool 和 Config 领域模型。
- schema 校验测试。
- 本周领域模型验收报告。

## 代码验收

我会检查工具风险等级是否完整，配置模型是否能表达用户可配置项，领域层是否仍然无基础设施依赖。

