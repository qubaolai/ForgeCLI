# ForgeCLI MVP 阶段滚动开发计划

## 1. 周期

- 起始日期：2026-06-17
- 当前滚动切片：**2026-07-23 至 2026-08-05（Agent 主循环 + 权限引擎，共 10 个工作日）**，详见 §8。
- 上一滚动切片：2026-06-29 至 2026-07-10（LLM 网关 MVP 闭环，§2–§7）。
- 当前阶段目标：按 ADR-0010 / ADR-0009 把 ForgeCLI 从「`AgentTurnService` 直连 gateway 的 stub
  回复」演进为「真实 ReAct 主循环 + 规则引擎裁决下的工具执行」。

本目录采用滚动排期。已完成日期保留历史记录。历史切片（06-17 至 07-20）聚焦 CLI 骨架、会话存储与
LLM 网关；当前切片（07-23 起）聚焦 Agent 主循环与权限引擎。Bash 沙箱、MCP、Skills、Sub-Agent、
上下文压缩、完整预算治理与企业治理继续留到后续 MVP 切片或 backlog。

## 2. 当前实现基线

当前代码已经具备：

- `src/forgecli + tests` 布局。
- Typer/Rich 单入口 CLI：裸 `forge` 进入 REPL，`--help` 和 `--version` 保留。
- `IntentRouter` 与 slash command registry。
- `application/config` 下的 `ConfigService`、`EffectiveConfig` 和 TOML store port。
- `application/llm/config` 下的 LLM 配置值对象、service 和 store port。
- `infrastructure/config` 与 `infrastructure/llm/config` 下的 TOML 适配器。
- `application/session` 与 `infrastructure/session` 下的事件存储、state 快照、session catalog 和 resume service。
- `application/agent_turn` 下的 `AgentTurnService` stub。
- `/status`、`/resume`、`/chat`、`/plan`、`/act` 等交互式入口的最小集成。

当前阶段性取舍：

- `/config` 已能管理供应商和模型参数；本阶段补齐当前模型和按用途显式模型覆盖的运行时语义与编辑入口（07-09）。
- 当前 MVP 不维护 `forge chat/status/models/config/resume` 这类 Typer 业务子命令；业务能力优先通过 REPL 内 slash command 暴露。
- 所有模型调用必须经过统一 `LlmGateway`，`AgentTurnService`、`AgentLoop` 和 CLI 不得直接依赖具体供应商 SDK。
- 默认测试不得访问真实网络或真实模型；真实 provider adapter 只能在显式配置凭证后手动验证。
- 本阶段按日完成非流式 `complete`、模型解析、usage/error/credential 边界、取消接线、凭证级 retry、`complete_structured`、AgentTurn 集成、OpenAI provider、streaming、tool calling 和响应缓存；熔断和完整预算扣减进入后续 backlog。
- AgentLoop（ADR-0010）若未落地，本阶段以 `AgentTurnService` 直连 gateway，作为偏差记录处理，不阻塞 gateway 边界验证。

## 3. LLM 网关阶段范围

本阶段必须包含：

- `LlmGateway`、`ModelProvider`、`ModelRequest`、`ModelResponse`、`ModelUsage` 等核心端口和 DTO。
- `FakeModelProvider`，用于 deterministic 单测和 REPL 集成测试。
- `ProviderRegistry`，只允许代码级注册 provider adapter。
- `ModelSelection`：`current_model` 与 `explicit_model` 两类选择。
- `ModelCatalogService` 运行时只读视图和配置覆盖合并。
- `ModelSelectionResolver`，负责当前模型与按用途显式模型覆盖的解析及 catalog 校验。
- `CredentialResolver` / credential ref 边界，确保明文 key 不进入配置、事件、日志或 fixture。
- `TokenEstimator` 近似实现、usage/cost 计量草稿和错误归一化。
- `complete_structured` 的 schema 校验最小能力。
- `AgentTurnService` 通过 gateway 获取 assistant message，并由 `AgentTurnService` 写入模型调用摘要和 usage 事件。
- `cancel_token` 从 `AgentTurnService` 到 gateway / provider 的取消接线（非流式协作式取消 + adapter 在途中止）。
- 同一 provider/model 内有界的凭证级 retry 最小运行时路径（默认测试用 fake transport）。
- `/config` 编辑当前模型、用途模型覆盖，以及任意已配置模型的 thinking 开放强度、
  默认强度和 mode；mode 直接控制是否启用，不存在额外支持字段，也不编辑当前 effort。
  `/thinking` 快速修改当前模型的 mode 与开放 effort。
- OpenAI provider adapter 的最小实现或清晰接口落点，默认测试不打网络。
- OpenAI provider adapter、streaming chunk 归一化、tool calling schema 转换和 tool result 回填。
- `LlmCacheController` 的 prompt 标注与可选响应缓存。

本阶段（当前 LLM 网关滚动切片）不包含。注意区分两类：

留待后续 MVP 滚动切片（仍属 MVP，不进入 backlog）：

- Agent 主循环（受控 ReAct）、模式策略、完整 ToolRuntime 与审批、上下文压缩与记忆、Sub-Agent 等 Agent 主功能。

默认排在 MVP 之后（见 [后续迭代 Backlog](../backlog.md)）：

- `RateLimiter`、`ProviderHealthRegistry` 熔断的真实策略。
- 完整 `BudgetGuard` 扣减、企业成本策略和跨 session 用量统计。
- 可观测性聚合。

## 4. 当前滚动计划拆分

| 日期 | 目标 |
| --- | --- |
| 2026-06-29 | LLM gateway 边界冻结：核心端口、DTO（全字段冻结）、完整 origin 枚举和错误类型 |
| 2026-06-30 | `FakeModelProvider`、provider registry、既有 llm 切片并轨和 `LlmGateway.complete` happy path |
| 2026-07-01 | 模型选择契约、移除旧档位类型和模型目录运行时视图 |
| 2026-07-02 | `ModelSelectionResolver`、当前模型/用途覆盖和能力校验 |
| 2026-07-03 | 凭证解析、安全边界和 provider 错误归一化 |
| 2026-07-06 | `TokenEstimator`、usage/cost 计量草稿和 context window 校验 |
| 2026-07-07 | `complete_structured`、`AgentTurnService` 集成和 `cancel_token` 接线 |
| 2026-07-08 | OpenAI adapter、凭证级 retry 和 provider 请求闭环 |
| 2026-07-09 | streaming 与 tool calling 归一化及 AgentTurn 回填 |
| 2026-07-10 | prompt/response cache、MVP 集成验收和文档收口 |

## 5. 每日验收格式

每天结束时需要提交：

- 今日完成的代码范围。
- 关键文件列表。
- 运行过的测试命令和结果。
- 与 ADR-0011 不一致的地方。
- 是否出现 gateway 直接落盘、application import 具体 SDK、凭证进入事件/日志/fixture 等边界问题。
- 保留的阶段性取舍和明日建议。

## 6. 日期目录

- [2026-06-17](2026-06-17/README.md)
- [2026-06-18](2026-06-18/README.md)
- [2026-06-19](2026-06-19/README.md)
- [2026-06-22](2026-06-22/README.md)
- [2026-06-23](2026-06-23/README.md)
- [2026-06-24](2026-06-24/README.md)
- [2026-06-25](2026-06-25/README.md)
- [2026-06-26](2026-06-26/README.md)
- [2026-06-27](2026-06-27/README.md)
- [2026-06-29](2026-06-29/README.md)
- [2026-06-30](2026-06-30/README.md)
- [2026-07-01](2026-07-01/README.md)
- [2026-07-02](2026-07-02/README.md)
- [2026-07-03](2026-07-03/README.md)
- [2026-07-06](2026-07-06/README.md)
- [2026-07-07](2026-07-07/README.md)
- [2026-07-08](2026-07-08/README.md)
- [2026-07-09](2026-07-09/README.md)
- [2026-07-10](2026-07-10/README.md)

## 7. 2026-07-20 ADR-0012 配置与 CLI 收口

在 07-10 历史滚动计划完成后，按 ADR-0012 增补以下验收口径：

- thinking 默认 mode / effort 跟随具体模型，保存到应用级 `llm.toml` 的模型条目；
  `/thinking` 的当前覆盖只存在进程内，`ModelRequest` 不提供显式覆盖。当前模型与用途
  覆盖继续保存到项目级 `forge.toml`。
- cache / circuit breaker / retry 统一为应用级网关配置，保存到 `llm.toml`，并在
  `/config` 提供查看与编辑入口。
- 输入框下方右侧持续显示当前项目模型与该模型的 thinking，修改或切换模型后重新渲染即刷新。
- `/thinking` 与 `/config` 共享模型目录读取；前者只更新当前进程内 override，不写入
  `llm.toml`，状态栏和网关请求必须立即使用新 mode/effort。
- ADR-0011、ADR-0012、详细设计、实现与离线测试使用同一配置归属和字段口径。

## 8. 2026-07-23 起：Agent 主循环 + 权限引擎滚动切片（切片 1）

### 8.1 周期与目标

- 范围：2026-07-23 至 2026-08-05，共 10 个工作日（跳过周末）。
- 阶段目标：按 ADR-0010（受控 ReAct `AgentLoop`）与 ADR-0009（规则引擎 + 模式预设）把
  ForgeCLI 从「`AgentTurnService` 直连 gateway 的 stub 回复」演进为「真实 ReAct 主循环 +
  可裁决的工具执行」。里程碑：四个权限模式下，Agent 能经能力门 / 裁决门安全地读文件、改文件、
  跑命令，所有副作用经 `AgentTurnService` 落盘。
- **本切片不含 Bash 沙箱**——沙箱是纵深防御的加固层，`rule-engine-only` 本就是无沙箱平台的
  合法回退态（ADR-0009 决策 14），故切片 1 以 rule-engine-only 交付，沙箱放切片 2。

### 8.2 前置清理（切片开工前）

- 清掉 `tests/2026_07_15/test_thinking_dialect.py` 的遗留红灯（budget dialect 已废，删测试 +
  同步 ADR-0012）。
- 把 `claude_test` 副本里已修的 4 处（`ChatMessage` 允许 tool-call-only、REPL EOF 收尾 +
  slash_command 落盘、`LlmGateway.stream` 提为 abstract、`/model` 返回值）回迁并 `make ci` 转绿。

### 8.3 切片范围（必须包含）

- ADR-0010：`LoopInput` / `LoopState` / `LoopDecision` / `LoopAction` / `LoopStop` +
  `LoopStopReason` 全集；`LoopHook` / `HookResult` / `LoopEvent` / `LoopEventBus` 骨架；
  `BuiltinAgentLoop`（先 answer + FINAL_ANSWER，再开放 tool request）；`AgentTurnService`
  改调 `AgentLoop` 替换 `GatewayReplier`；streaming 渲染与 Ctrl-C 取消接线。
- ADR-0009：`SessionMode` 改 `PLAN/ACCEPT_EDITS/AUTO/FULL_ACCESS`（默认 accept_edits）+ root
  启动校验；命令规范化解析器（复合拆解 / 包装器剥离 / 替换扫描 / 只读集 / 文件操作集 / 危险目标
  识别）；规则引擎 `deny→ask→allow` + 内置高危 deny（命令 + 目录黑名单）+ 能力门/裁决门 +
  执行流水线骨架；ApprovalService + 学习式授权（once/always/session/deny）；glob 规则配置语法
  （Bash 模式 + gitignore 路径锚定）+ 出区读写裁决。
- ADR-0004：`ToolRuntime` + `ToolRegistry` + 内置工具（read_file / glob / grep / write / edit /
  shell）+ 文件工作区边界强制（realpath 防 `..`/symlink/hardlink 逃逸）+ `ActionDispatcher`。

### 8.4 切片范围（不包含，留切片 2 或 backlog）

- **Bash 沙箱**（Seatbelt / bwrap + 探测回退 + escalation）→ 切片 2。
- MCP、Skills、Sub-Agent、上下文压缩与记忆 → 后续 MVP 切片。
- 分类器式 auto 审查、`RateLimiter` / `BudgetGuard` 真裁决、可观测性聚合 → backlog。

### 8.5 每日拆分

| 日期 | 目标 | 主要 ADR |
| --- | --- | --- |
| 2026-07-23 | `AgentLoop` 边界冻结：`LoopInput/State/Decision/Action/Stop` 全字段 + `LoopStopReason` 全集 + `LoopHook/LoopEvent/LoopEventBus` 骨架，仅冻契约不实现循环体 | 0010 §4/§6/§7 |
| 2026-07-24 | `BuiltinAgentLoop` 最小实现（answer + `FINAL_ANSWER`）+ `AgentTurnService` 改调 `AgentLoop` 替换 `GatewayReplier` + fake provider 测试 + streaming 渲染 / Ctrl-C 取消接线 | 0010 §5/§13(1-5) |
| 2026-07-27 | `SessionMode` 改四模式（默认 accept_edits）+ `/chat` 别名 + mode/status/menu 同步；root 启动校验 | 0009 §2/§6 |
| 2026-07-28 | 命令规范化解析器：复合拆解 + 包装器剥离 + 替换扫描 + 只读集 / 文件操作集 / 危险目标识别（纯函数，重绕过用例测试） | 0009 §4/§5 |
| 2026-07-29 | 规则引擎 `deny→ask→allow` + 内置高危 deny（命令 + 目录黑名单）+ 能力门/裁决门 + 执行流水线骨架 | 0009 §3/§7/§8/§14 |
| 2026-07-30 | `ToolRuntime` + `ToolRegistry` + 只读工具（read_file/glob/grep）经能力门进 `tool_catalog`；`AgentLoop` 开放 tool request，`ActionDispatcher` 执行、observation 回填 | 0004 / 0010 §13(6-7) |
| 2026-07-31 | 写/编辑工具（write/edit）+ 文件工作区边界强制（realpath 防逃逸）+ accept_edits 文件编辑与文件操作命令自动放行 | 0009 §5/§11 |
| 2026-08-03 | shell 工具经规则引擎裁决 + `ApprovalService`（once/always/session/deny）+ 学习式授权落项目配置 | 0009 §9 |
| 2026-08-04 | glob 规则配置语法（Bash 模式 + gitignore `//`/`~/`/`/`/`./` 锚定）+ 出区读写裁决 + 种子 allow 探测 | 0009 §10 |
| 2026-08-05 | 集成验收矩阵（四模式 × 各类动作）+ 安全边界测试（复合/替换/包装器绕过用例）+ ADR/详细设计/路线图/backlog 收口 | 全部 |

### 8.6 每日验收格式

沿用 §5，并增补：

- `AgentLoop` 不 import CLI / filesystem / shell / git / 具体 LLM SDK；只产出
  `LoopDecision/LoopAction/LoopStop`，副作用一律经 `AgentTurnService`。
- 每条 shell 命令的裁决可复现（同一命令稳定给出 deny/ask/allow），绕过写法有对应测试。
- 高危 deny 在进入执行前拦下（`rm -rf ./*`、写 `.git/hooks` 等），四模式含 `full_access` 均拒。
- 副本先行：当日实现先在 `claude_test` 副本完成并 `make ci` 转绿，再列改动文件。

### 8.7 切片 2 草案（2026-08-06 起，Bash 沙箱）

待切片 1 收口后细化。范围：沙箱 adapter（macOS Seatbelt profile / Linux bwrap bind 参数）+
启动探测与 `rule-engine-only` 回退 + 执行流水线接沙箱（deny → 沙箱执行 → 越界 escalation）+
`full_access` 越界预授权 + 沙箱边界与 `Read`/`Edit` 路径规则合并 + 跨平台测试。口径见 ADR-0009
决策 14。

### 8.8 日期目录（切片 1）

- [2026-07-23](2026-07-23/README.md)
- [2026-07-24](2026-07-24/README.md)
- [2026-07-27](2026-07-27/README.md)
- [2026-07-28](2026-07-28/README.md)
- [2026-07-29](2026-07-29/README.md)
- [2026-07-30](2026-07-30/README.md)
- [2026-07-31](2026-07-31/README.md)
- [2026-08-03](2026-08-03/README.md)
- [2026-08-04](2026-08-04/README.md)
- [2026-08-05](2026-08-05/README.md)
