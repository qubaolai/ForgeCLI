# 2026-07-01：模型选择契约、模型目录与旧档位清理

## 今日目标

基于 06-30 已完成的显式模型 happy path，收敛 ADR-0011 的模型选择语义：默认使用当前模型，按用途的显式覆盖作为可选能力，并移除代码和 DTO 中旧的模型档位概念。

## 当前基线

- 已有 `ModelSelection`、`ExplicitModelSelection` 和 `CurrentModelSelection` 占位类型，但仍保留 `TierModelSelection`。
- `DefaultLlmGateway` 目前只接受显式 provider/model，尚未解析当前模型。
- `ProviderRegistry`、`FakeModelProvider` 和 `LlmGateway.complete` 已可支撑 deterministic happy path。
- 既有 LLM 配置服务已经能读取 provider/model 元数据和默认模型配置。

## 开发指导

- 将 `ModelSelection` 收敛为 `current_model` 与 `explicit_model` 两类，删除 `tier`、`allow_fallback`、`fallback_policy` 相关字段和语义。
- 保留 `origin` 作为调用用途标签；它用于提示词、参数、usage、审计和限流分类，不选择模型。
- 明确 mode policy 属于 `AgentTurnService` 的动作权限、工具和审批规则，不参与模型选择。
- 定义 `ModelCatalogService` 的只读模型视图：provider/model 存在性、context window、structured output、tool calling、thinking 和 allowlist 元数据均从 catalog 获取。
- 冻结 `ModelSelectionResolver` 的输入输出契约，但不在今日实现完整配置解析和 `/config` 编辑。

## 非目标

- 不实现用途覆盖的运行时解析。
- 不实现凭证解析、模型能力前置检查、自动模型切换或 provider retry。
- 不接真实 provider，不实现 streaming、tool calling 和缓存。

## 最终产物

- 清理后的 `ModelSelection` 类型契约。
- `ModelCatalogService` 只读视图接口和模型元数据边界。
- `ModelSelectionResolver` 接口及与 gateway 的依赖方向说明。
- 单元测试覆盖 current/explicit 字段互斥和旧档位类型不可用。

## 验收重点

- gateway、provider adapter 和 application 不再依赖模型档位概念。
- `current_model` 不携带 provider/model；`explicit_model` 必须同时携带 provider/model。
- `origin` 与 mode policy 的职责清晰，LLM 不能提出模型升级或切换请求。
- `ModelCatalogService` 是单模型能力的唯一事实来源。

## 验收命令

```bash
make ci
poetry run pytest tests/2026_07_01
```
