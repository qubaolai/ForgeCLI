# ForgeCLI 工程规范流程

## 1. 研发原则

ForgeCLI 面向生产环境和真实代码仓库，工程流程必须优先保证可控、可恢复、可审计和可维护。

基本原则：

- 先设计后实现。
- 小步提交，持续验证。
- 领域逻辑与基础设施解耦。
- 高风险行为默认显式审批。
- 所有重要决策可追溯。
- 文档、测试和代码同步演进。

## 2. 需求流程

### 2.1 需求输入

每个需求必须包含：

- 背景和用户场景。
- 目标和非目标。
- 交互模式影响。
- 权限和安全影响。
- 存储和兼容性影响。
- 验收标准。

### 2.2 需求分级

| 等级 | 定义 | 示例 |
| --- | --- | --- |
| P0 | 阻塞核心使用或安全风险 | session 无法恢复、越权写文件 |
| P1 | 核心能力缺失或严重错误 | plan 模式误写文件 |
| P2 | 重要体验和稳定性问题 | 输出不清晰、工具错误分类不足 |
| P3 | 增强能力 | 新 Skill、新 MCP server |

### 2.3 RFC

以下变更必须写 RFC：

- 新增 Agent 模式。
- 修改事件 schema。
- 修改权限策略。
- 引入新存储后端。
- 引入 Multi-Agent 能力。
- 改变 MCP 或 ToolSpec 协议。

RFC 内容：

- 问题背景。
- 方案对比。
- 推荐方案。
- 数据结构变化。
- 兼容性影响。
- 测试计划。
- 回滚方案。

## 3. 架构决策记录

使用 ADR 记录不可轻易逆转的决策。

ADR 模板：

```md
# ADR-0001: 决策标题

## Status

Accepted

## Context

为什么需要这个决策。

## Decision

做什么选择。

## Consequences

收益、代价、风险和后续影响。
```

必须记录的 ADR：

- 本地存储选型：JSONL + state。
- 架构分层：轻量 DDD。
- Agent 开发框架选型：自有控制面 + 可替换 workflow adapter。
- Agent 模式策略。
- Sub-Agent 和 Multi-Agent 边界。
- Tool/MCP 统一注册。

## 4. 分支与提交规范

### 4.1 分支

建议：

- `main`：稳定分支。
- `develop`：集成分支，可选。
- `feature/<scope>-<name>`：功能分支。
- `fix/<scope>-<name>`：缺陷修复。
- `release/<version>`：发布分支。

### 4.2 提交

采用 Conventional Commits：

- `feat: add session event store`
- `fix: reject writes in plan mode`
- `docs: add overview design`
- `test: cover state replay`
- `refactor: split tool policy resolver`

每个提交应尽量小且可回滚。

## 5. 代码规范

### 5.1 Python

建议：

- Python 3.13+
- `Poetry` 做依赖管理和打包，提交 `poetry.lock`。
- `ruff` 做 lint 和 format。
- `mypy` 做类型检查。
- `pytest` 做测试。
- `pydantic` 做边界数据校验。
- 项目采用 `src/forgecli + tests` 布局。

### 5.2 代码组织

要求：

- `domain` 不依赖 `infrastructure`。
- application 层只依赖 `AgentWorkflow` 接口，不直接依赖 LangGraph、LangChain、AutoGen。
- application service 负责用例编排，不堆业务规则。
- 基础设施适配器必须通过接口暴露能力。
- 工具结果必须结构化，不直接把 stdout 当业务对象。
- Event schema 变更必须有版本号。
- 第三方 Agent 框架不得绕过 Tool Registry、Policy Context 或 Event Store。
- MVP 阶段仅创建当前阶段需要的模块，不提前创建未来阶段空 adapter。

### 5.3 异常处理

要求：

- 不吞异常。
- 领域错误使用明确错误类型。
- CLI 层输出用户可理解的信息。
- 详细错误写入日志或 artifact。
- 工具失败不能破坏 session。

## 6. 测试规范

### 6.1 测试分层

| 类型 | 目标 | 示例 |
| --- | --- | --- |
| Unit | 验证领域逻辑 | ModePolicy、EventStore、ContextManager |
| Integration | 验证模块协作 | ToolRuntime、MCP mock、Skill loader |
| E2E | 验证真实用户流程 | plan -> act -> test -> resume |
| Security | 验证安全边界 | 越权路径、危险命令、敏感信息 |
| Regression | 防止已修复问题复发 | 历史 bug 用例 |

### 6.2 覆盖率要求

MVP：

- domain 单元测试覆盖率不低于 80%。
- application 核心流程不低于 70%。

GA：

- domain 不低于 90%。
- 关键 use case 有 E2E。
- 所有 P0/P1 bug 必须有回归测试。

### 6.3 测试数据

要求：

- 使用 fixtures 构造 demo repo。
- 不依赖用户真实 HOME 目录。
- 不使用真实 token。
- 外部服务使用 mock 或本地 fake server。
- 测试写入临时目录。

## 7. 安全规范

### 7.1 权限边界

默认策略：

- 只读允许。
- 写 workspace 内文件需 act/auto。
- workspace 外写入默认拒绝。
- 删除、reset、push、发布强制确认。
- 网络访问默认询问。

### 7.2 命令风险分类

Shell 执行前必须分类：

- 只读：`ls`、`git status`、`pytest --collect-only`。
- 写入：测试生成缓存、构建产物、格式化。
- 网络：安装依赖、curl、包管理器。
- destructive：删除、reset、clean、权限修改。
- external：push、发布、发消息。

无法分类时按更高风险处理。

### 7.3 敏感信息

要求：

- 日志默认脱敏环境变量中的 token、key、secret。
- 不把凭证写入 memory。
- 工具输出 artifact 需要敏感信息扫描。
- 用户可执行 `/redact` 或配置脱敏规则。

## 8. 发布流程

### 8.1 版本号

使用 SemVer：

- `MAJOR`：破坏性变更。
- `MINOR`：新增兼容功能。
- `PATCH`：兼容修复。

### 8.2 发布步骤

1. 冻结 release 分支。
2. 跑完整 CI。
3. 跑 E2E 和安全测试。
4. 更新 changelog。
5. 更新文档。
6. 构建安装包。
7. 安装测试。
8. 标记 tag。
9. 发布。
10. 监控反馈。

### 8.3 回滚

必须支持：

- 发布包撤回。
- 文档标记已知问题。
- 配置 feature flag 关闭高风险能力。
- 用户本地数据向后兼容读取。

## 9. 可观测性规范

### 9.1 本地诊断

提供：

- `forge doctor`
- `forge inspect <session_id>`
- `forge logs`
- `forge export-debug-bundle`

debug bundle 应包含：

- 配置摘要。
- session 状态。
- 事件日志脱敏版本。
- 最近错误。
- 工具调用摘要。

### 9.2 指标

企业模式可采集：

- turn latency。
- tool latency。
- model latency。
- tool failure rate。
- approval reject rate。
- resume success rate。
- context compaction count。

默认不上传用户代码和完整对话。

## 10. 文档规范

必须维护：

- 用户指南。
- 命令参考。
- 配置参考。
- MCP 接入指南。
- Skill 编写指南。
- 安全模型说明。
- 架构文档。
- Changelog。

设计变更必须同步更新：

- 相关设计文档。
- 示例配置。
- 测试用例。
- 用户可见行为说明。

## 11. Definition of Done

一个功能完成必须满足：

- 需求和验收标准明确。
- 设计已评审。
- 代码已实现。
- 单元测试和必要集成测试已添加。
- CLI 用户体验已验证。
- 权限和安全影响已评估。
- 文档已更新。
- CI 通过。
- 无 P0/P1 已知问题。

## 12. 生产发布准入

GA 前必须满足：

- 核心 E2E 全通过。
- P0/P1 问题清零。
- 安全审查通过。
- 默认配置安全。
- session 恢复成功率在测试矩阵中达到 100%。
- 工具调用审计完整。
- 安装、升级、卸载测试通过。
- 文档覆盖首版核心路径。

## 13. 变更兼容性

### 13.1 Event schema

事件 schema 必须带版本号。新增字段必须兼容旧 reader。删除字段必须经过 deprecation。

### 13.2 State schema

`state.json` 可重建，但仍需迁移策略。读取失败时应尝试从 `events.jsonl` replay。

### 13.3 Config schema

配置变更必须：

- 保留默认值。
- 输出清晰迁移提示。
- 支持 `forge config migrate`。

## 14. 企业采用建议

企业内部落地时建议增加：

- MCP server allowlist。
- 禁止默认联网。
- 审批策略配置模板。
- debug bundle 脱敏检查。
- 内部模型 provider 配置。
- 内部 Skill 仓库。
- 安全基线扫描。
- 审计日志归档。

这些能力不必进入 MVP，但设计上不能阻断。
