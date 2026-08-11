# 2026-08-07：工具运行时、提示词编译与安全规则引擎纵向实现

## 目标

在不把 ADR 示例清单误作最终产品配置的前提下，完成一条可运行、可测试且默认保守失败的纵向链路：

```text
Turn 开始
  → 计算一次 ToolCatalog
  → 受限读取可信根 FORGE.md
  → 编译并冻结 PromptSnapshot
  → ModelRequest 复用同一 prompt/catalog
  → tool call
  → prepare ToolPlan
  → capability analyzer
  → PolicyEngine
  → approval（如需）后完整重验
  → recovery guard
  → 签发短期、一次性 ExecutionAuthorization
  → ToolRuntime 统一校验并执行
```

## 已实现范围

### 工具系统

- `ToolSpec`、`ToolPlan`、闭集 `Capability`、稳定 hash 和不可信 `ToolResult`。
- `ToolRegistry` 与外部 predicate 计算的 `ToolCatalog`；registry 不认识 session mode。
- `Tool.prepare()` 与 `Tool.execute()` 三段式契约；prepare 不执行进程、网络或写入。
- `ToolRuntime` 强制非空授权、HMAC issuer proof、有效期、执行画像、spec/capability/target 上界和一次性消费。
- 本地 `PlanTool`、`ReadFileTool`、`WriteFileTool`、薄 `ShellTool`。
- `BuiltinAgentLoop` 支持 model tool call → tool result → 再次 model call，并在同一 turn 复用 prompt/catalog。

### 安全规则引擎

- 按 `Capability` 分派的 analyzer registry；安全模块不依赖具体工具实现。
- 可注入 `PolicyRule` 和固定优先级：`DENY > MANDATORY ASK > ASK > ALLOW`。
- plan mode 的 catalog 能力门，以及 mode/execution profile 的裁决门。
- POSIX、cmd、PowerShell 核心复合命令解析骨架，包含包装器、嵌套解释器、命令替换、heredoc 和脚本载荷。
- `ShellCapabilityAnalyzer` 提取读写删、脚本、网络、未知与不可逆外部效果；复合命令在首个进程启动前整体预检。
- 人类批准不是授权：批准后重新 prepare、比较 plan hash、重新分析和裁决，再建立恢复绑定并签发授权。
- 默认 `NonMutatingRecoveryGuard`：恢复存储尚未装配时，写入、Shell、脚本和 UNKNOWN 一律返回
  `recovery_unavailable`，不会静默直写真实工作区。

### 提示词系统

- `PromptSnapshot`、来源摘要和由 profile/version/text 派生的 fingerprint。
- 代码拥有、带版本、可在组合根替换的 `MainAgentTemplate`，而不是在 loop 中拼固定大字符串。
- 固定信任/缓存顺序：核心身份与交互契约 → FORGE.md → 运行事实。
- `FilesystemProjectInstructionReader`：只读可信 root 顶层文件，拒绝越界 symlink、NUL、非 UTF-8；
  单文件 32 KiB、总计 64 KiB，显式截断和 sentinel 转义。
- 主 Agent 与 `SafetyClassifierPromptBuilder` 独立 profile；分类器不继承 FORGE.md、工具目录或会话历史。
- 普通事件和默认终端流只需记录 profile/version/fingerprint/source digest，不要求持久化 prompt 原文。

### 终端 UI

- ASK 通过 Rich/TTY 安全面板展示，默认选中“仅本次批准”；非 TTY 返回 `approval_required`，不会自动批准。
- 内部审批展示绑定 plan/view hash；默认 HITL 面向动作展示当前模式、完整工作区、原始命令/动作与
  有后果的绝对目标，不把内部 hash、风险标签或执行画像堆进确认界面。
- 文件正文、payload、凭证类字段只展示字节数和 digest，避免敏感原文进入终端滚动历史。
- Shell inline/heredoc 脚本展示分析绑定的完整内容；脚本文件内容尚未在 prepare 阶段冻结时，审批
  只能拒绝，并返回 `approval_presentation_incomplete`，不会仅凭脚本路径批准。
- 目标不封闭或存在变更后果时，面板明确展示目标分类计数、完整清单和恢复要求；批准只对当前 plan
  有效，之后仍执行完整重验和恢复门。
- 工具生命周期以请求、预检、裁决、待审批、执行、完成/阻断反馈；过程 UI 不接收不可信工具输出。
- 默认终端隐藏审批结果、批准后重验、执行开始和完成四类高频内部过程行；事件仍在 application 层发布，
  阻断信息仍会显示。
- 人类批准后的展示 hash 进入 HMAC 授权信封，并在 ToolRuntime 校验；展示事实变化会使批准失效。
- 输入框状态栏显示模式、模型、隔离和当前工具数；`/status` 现读当前 mode 的工具目录、审批方式、
  恢复可用性与 prompt profile 版本。

### 静态边界

- `scripts/check_architecture.py` 解析 AST，检查 domain/application/infrastructure/interfaces 依赖方向。
- 额外禁止 tool mechanism import security/adapter，以及 security import 具体 builtin tool。
- `make architecture` 已加入 `make ci`。

## ADR 示例与产品配置的边界

以下内容明确不是最终硬编码策略：

- ADR 中出现的命令、路径、工具名和危险语料；
- 主 Agent 的示例提示词措辞；
- MVP `baseline_semantics()` 中的常见命令集合；
- 当前 `default_policy_rules()` 的最小规则包。

实现把机制和策略拆开：命令语义由 `CommandSemantics` 注入，安全策略由 `PolicyRule` 注入，主 Agent
文案由版本化 `MainAgentTemplate` 注入。未知命令或未覆盖规则不会被跳过，而是进入 UNKNOWN/ASK 路径。

真正不可配置的是安全不变量：授权前置、能力只能收缩、Hard Deny 不可被模式覆盖、批准后必须重验、
恢复与授权互不替代、工具输出不可信，以及一个 turn 内 prompt/catalog 冻结。

## 当前安全限制与后续工作

本次不宣称以下能力已经完成：

- ADR-0015 的 RecoveryStore、checkpoint、冲突检测和崩溃恢复；当前以默认 guard 阻断变更。
- ADR-0014 的 sandbox provider、实例生命周期和资源限制；执行画像如实标记 `NO_SANDBOX`。
- 受限 always/session 学习规则和策略持久化；当前 UI 仅支持对完整重验后的同一 plan 批准一次。
- executable realpath/文件身份签名、完整 PATH 工具链信任、所有 Shell 方言语法和生产级脚本分析器。
- artifact store 与超大输出落盘；当前结果只做有界截断并显式标记。
- Background Safety Classifier 的 LLM adapter/cache；仅完成隔离 profile，未知/低置信度路径仍 ASK。

这些缺口都采用保守结果，不通过“临时 Allow”或隐藏旁路模拟完成。

## 验证

仓库当前环境没有全局 Poetry，因此使用仓库 `.venv` 中等价工具执行：

```text
.venv/bin/python scripts/check_architecture.py
.venv/bin/ruff format --check src tests scripts
.venv/bin/ruff check src tests scripts
.venv/bin/mypy src/forgecli
.venv/bin/pytest -q
```

覆盖重点包括 prompt 指纹与层次、FORGE.md 越界/截断、plan catalog、授权缺失/画像变化/单次消费、
审批面板完整事实与非 TTY 安全默认、敏感正文脱敏、工具活动状态、恢复缺失阻断写入、root 删除整体
拒绝、heredoc 脚本提取、复合命令预检，以及同 turn 工具往返复用同一 prompt/catalog。

长任务单轮循环步数和工具调用次数分别以 `9999` 作为最终熔断上限；工具次数单独计数并进入
`LoopBudgets`，不再沿用早期 MVP 的 8 步临时限制。
