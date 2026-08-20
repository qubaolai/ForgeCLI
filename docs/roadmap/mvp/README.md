# ForgeCLI MVP 阶段滚动开发计划

## 1. 周期

- 起始日期：2026-06-17
- 当前滚动切片：**2026-08-19（本地 Web 控制面）**，详见 [当日计划](2026-08-19/README.md)。
- 上一滚动切片：2026-06-29 至 2026-07-10（LLM 网关 MVP 闭环，§2–§7）。
- 当前阶段目标：按 ADR-0025 完成本地 Web 控制面；保留已有 Agent/ToolRuntime、审批、
  恢复与 session 语义，只替换交互和展示适配器。

本目录采用滚动排期。已完成日期保留历史记录。历史切片（06-17 至 07-20）聚焦 CLI 骨架、会话存储与
LLM 网关；07-23 至 07-29 的 Agent 主循环与权限引擎计划保留为前置历史记录。当前切片（07-30 起）
聚焦 Agent Shell 执行安全机制。MCP、Skills、Sub-Agent、上下文压缩、完整预算治理与企业治理继续留到
后续 MVP 切片或 backlog；本切片只实现 Shell 执行安全所需的沙箱最小闭环。

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
- [2026-08-07](2026-08-07/README.md)
- [2026-08-10](2026-08-10/README.md)
- [2026-08-19](2026-08-19/README.md)

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

## 8. 历史切片：2026-07-23 至 2026-07-29

本节保留 7/23–7/29 的历史开发记录。原计划中 7/30 之后的排期已由 §9 的
ADR-0013/ADR-0014 计划替代，不再作为当前开发或验收依据。

### 8.1 周期与目标

- 范围：2026-07-23 至 2026-08-05，共 10 个工作日（跳过周末）。
- 阶段目标：按 ADR-0010（受控 ReAct `AgentLoop`）与 ADR-0009（规则引擎 + 模式预设）把
  ForgeCLI 从「`AgentTurnService` 直连 gateway 的 stub 回复」演进为「真实 ReAct 主循环 +
  可裁决的工具执行」。里程碑：四个权限模式下，Agent 能经能力门 / 裁决门安全地读文件、改文件、
  跑命令，所有副作用经 `AgentTurnService` 落盘。
- **历史切片当时不含 Bash 沙箱**——沙箱是纵深防御的加固层，`rule-engine-only` 曾作为无沙箱平台的
  合法回退态；该阶段取舍已由当前 §9 和 ADR-0014 的沙箱计划替代。

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

### 8.4 历史阶段范围（当时不包含，现已由 §9 覆盖）

- **Bash 沙箱**（Seatbelt / bwrap + 探测回退 + escalation）当时留到后续切片；当前由 §9 的
  2026-08-04 计划覆盖。
- MCP、Skills、Sub-Agent、上下文压缩与记忆 → 后续 MVP 切片。
- 分类器式 auto 审查当时留后续切片；当前由 §9 的 2026-08-03 计划覆盖。
- `RateLimiter` / `BudgetGuard` 真裁决、可观测性聚合 → backlog。

### 8.5 历史每日拆分

| 日期 | 目标 | 主要 ADR |
| --- | --- | --- |
| 2026-07-23 | `AgentLoop` 边界冻结：`LoopInput/State/Decision/Action/Stop` 全字段 + `LoopStopReason` 全集 + `LoopHook/LoopEvent/LoopEventBus` 骨架，仅冻契约不实现循环体 | 0010 §4/§6/§7 |
| 2026-07-24 | `BuiltinAgentLoop` 最小实现（answer + `FINAL_ANSWER`）+ `AgentTurnService` 改调 `AgentLoop` 替换 `GatewayReplier` + fake provider 测试 + streaming 渲染 / Ctrl-C 取消接线 | 0010 §5/§13(1-5) |
| 2026-07-27 | `SessionMode` 改四模式（默认 accept_edits，纯运行时状态不落盘）+ `Tab`/`Shift+Tab` 切档 + mode/status/menu 同步；root 启动校验 | 0009 §2/§6 |
| 2026-07-28 | 命令规范化解析器：复合拆解 + 包装器剥离 + 替换扫描 + 只读集 / 文件操作集 / 危险目标识别（纯函数，重绕过用例测试） | 0009 §4/§5 |
| 2026-07-29 | 规则引擎 `deny→ask→allow` + 内置高危 deny（命令 + 目录黑名单）+ 能力门/裁决门 + 执行流水线骨架 | 0009 §3/§7/§8/§14 |
| 2026-07-30 至 2026-08-05 | 原权限设计排期，已废弃 | 不作为当前计划或验收依据 |

### 8.6 每日验收格式

沿用 §5，并增补：

- `AgentLoop` 不 import CLI / filesystem / shell / git / 具体 LLM SDK；只产出
  `LoopDecision/LoopAction/LoopStop`，副作用一律经 `AgentTurnService`。
- 每条 shell 命令的裁决可复现（同一命令稳定给出 deny/ask/allow），绕过写法有对应测试。
- 高危 deny 在进入执行前拦下（`rm -rf ./*`、写 `.git/hooks` 等），四模式含 `full_access` 均拒。
- 副本先行：当日实现先在 `claude_test` 副本完成并 `make ci` 转绿，再列改动文件。

### 8.7 历史切片 2 草案（已由 §9 取代）

原计划将 Bash 沙箱排到 2026-08-06 之后；该计划已废弃。当前沙箱能力探测、实例生命周期、
Provider 和 `/add-dir` 以 §9 和 ADR-0014 为准。

### 8.8 历史日期目录

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

**<font color="red">0723-0729实现内容为废弃adr0009部分实现, 已经作为留存 暂时在开发分支清除</font>**

## 9. 2026-07-30 至 2026-08-05：Agent Shell 执行安全机制

### 9.1 周期与目标

- 周期：2026-07-30 至 2026-08-05，共 5 个工作日，跳过周末。
- 目标：完成 ADR-0013 与 ADR-0014 定义的 Agent Shell 执行安全闭环。
- 交付结果：Agent 能在四种 mode 下通过能力门、命令安全决策和执行环境约束安全执行或拒绝 Shell 请求。
- 计划边界：本切片不重做通用 Tool Registry、MCP、Skills 或 Agent 主循环；只为已有工具调用链提供
  `PlanTool`、`ShellTool` 和安全协调器所需的最小接入。

旧 §8.5 中 2026-07-30 至 2026-08-05 的排期属于前一套权限设计的历史草案；从本节开始，以 ADR-0013、
ADR-0014 和本节计划为准。

### 9.2 必须交付

- `plan` mode 的能力门：模型只看见 `PlanTool` 和只读工具。
- `ToolRequestCoordinator` / `ToolAuthorizationService` 的最小安全调用链；Shell 请求按
  `EXECUTE_SHELL` 能力分派给 `ShellCapabilityAnalyzer`。
- 只负责执行的薄 `ShellTool`，不得在工具内部重复实现策略裁决。
- 原始 Shell Parser、AST / `CommandPlan` 和整体预检。
- `allow`、`deny`、`ask` 和 Hard Deny 规则决策。
- `EXECUTE_SCRIPT` 能力识别，覆盖 Python、Shell、Node、测试命令和 heredoc。
- 临时脚本、heredoc、`-c` 内联代码和可发现入口依赖的确定性 `ScriptAnalyzer`。
- POSIX、`cmd.exe` 和 PowerShell 核心方言解析及嵌套解释器处理。
- 强沙箱能力探测、临时实例创建和无沙箱降级档案。
- 复用 SRT 的 Provider 边界、`/sandbox` 用户开关、平台安装指引和自测降级。
- 跨平台 `ProtectedPathPolicy`，覆盖 Forge 应用根目录、敏感目录和路径别名。
- 无沙箱 `auto` 的 Background Safety Classifier 接口、结构化输出和缓存。
- 通过现有 LLM Gateway 调用分类器的最小 adapter；默认测试使用 fake classifier。
- `/add-dir <path> [--write]`、`--list`、`--remove` 和策略版本更新。
- 阻塞式 `ASK`、分类器 fail-safe 和受限的 once/session/workspace/always 学习式授权。
- 人类批准后的全量绑定重验；计划、目标、策略或执行环境变化时旧批准失效并重新裁决。
- WSL2 Linux Provider 边界，以及持久 Shell 策略变化后的 cwd/环境变量恢复。
- 四种 mode、平台能力和脚本类型的集成验收矩阵。

### 9.3 非目标

- 完整重构通用 Tool Registry 或 MCP Tool Registry。
- 新增 Skills、Sub-Agent、Multi-Agent 和上下文压缩能力。
- 实现所有 Shell 语法；不支持的语法必须安全地进入 `ASK`。
- 在本切片内实现生产级虚拟机隔离。
- 让 LLM 覆盖 Hard Deny 或直接拥有执行授权权。
- 让 LLM 自行调用 `/add-dir` 扩大宿主机访问范围。

### 9.4 每日拆分

| 日期 | 目标 | 主要产物 | 验收重点 |
|---|---|---|---|
| 2026-07-30 | 冻结调用边界和能力门 | `PlanTool`、`ToolCatalog` mode 过滤、`ToolRequestCoordinator`、`ShellTool` 薄接口 | `plan` 只暴露计划和只读工具；Shell 请求先经过安全协调器；拒绝结果可结构化回填 LLM |
| 2026-07-31 | 完成 Shell 解析和命令事实提取 | 方言 AST / `CommandPlan`、POSIX/cmd/PowerShell 复合命令、嵌套解释器、heredoc、路径和能力提取 | `cat a.txt | grep b && rm -rf /` 整体预检并拒绝；Windows 连接符、变量、编码命令和包装器不绕过解析 |
| 2026-08-03 | 完成规则决策和脚本风险路径 | `allow/deny/ask`、Hard Deny、四种 mode、`ScriptAnalyzer`、阻塞式 ASK、学习式授权、无沙箱分类器 adapter、哈希缓存 | `ASK` 未经人类确认不调用 ShellTool；分类器失败仍阻塞；always 规则不泛化；无沙箱 auto 对未确定脚本仍调用分类器 |
| 2026-08-04 | 完成沙箱生命周期和资源授权 | `SandboxManager`、SRT Provider 探测、能力自测、`ProtectedPathPolicy`、临时实例、`SandboxPolicy`、`/sandbox`、`/add-dir`、持久 Shell 状态恢复 | 默认不启用且不静默安装；Forge 根目录和平台敏感路径不能通过别名或授权绕过；启用后按实例创建；WSL2 与 cwd/环境恢复有测试 |
| 2026-08-05 | 完成端到端验收和文档收口 | 四 mode × 平台 × 脚本矩阵、红蓝对抗、静态分析、审计事件、错误回填、回归测试、ADR/设计同步 | `make ci` 通过；红蓝 P0/P1 用例、Hard Deny、ASK 阻塞、分类器失败、解析失败、沙箱不可用和 `/add-dir` 变更均有明确结果 |

### 9.5 每日验收要求

沿用 §5 的每日验收格式，并额外记录：

- 当日实现是否保持 `ShellTool` 只执行、不负责安全判断。
- `plan` mode 的工具目录快照及不可用工具兜底错误。
- Shell 解析失败、分类器异常和沙箱不可用时的 fail-safe 结果。
- 每个脚本自动执行结论对应的环境等级、策略版本和缓存键。
- `/add-dir` 的用户来源、访问模式、策略版本和后续实例可见性。
- 新脚本、变更脚本和未知脚本的分类器触发条件与缓存失效原因。
- 脚本内容快照、内容哈希、依赖发现和静态分析不完整时的降级行为。
- `/sandbox` 的用户来源、Provider 版本、成熟度、自测结果及启停对后续实例的影响。
- 红蓝对抗用例的攻击输入、预期裁决、ShellTool 调用计数、canary 影响和审计证据。
- 持久 Shell 策略重建后的 cwd/环境变量恢复和 WSL2 主机路径边界。
- 不修改通用 Tool Registry、MCP 或旧权限设计的边界说明。

### 9.6 里程碑验收

五日切片完成的最低标准：

```text
LLM 请求 Shell
  ↓
plan/模式能力门
  ↓
Shell AST / CommandPlan
  ↓
allow / deny / ask + Hard Deny
  ↓
确定性脚本分析（适用时）
  ↓
必要时 LLM 风险评估
  ↓
ShellTool
  ↓
SandboxProvider / SandboxInstance 或 NoSandboxProvider
  ↓
执行、审计、结果回填
```

任一环节绕过统一入口、Hard Deny 可被覆盖、无沙箱分类器失败仍自动放行，均不得视为本切片完成。

## 10. 2026-07-31 实际实现: 按架构层次而非日切片交付

§9 的 07-30 至 08-05 日切片没有按日执行. 实际实现在 2026-07-31 一次完成, 并且范围与
§9 不同 —— 按 ADR-0004 (2026-07-31 细化修订) 与 ADR-0015 (新增) 一并落地, 沙箱层缓期.

### 10.1 与 §9 计划的差异

| 项 | §9 计划 | 实际 |
|---|---|---|
| 排期 | 五个工作日, 按日切片 | 一次交付, 按架构层次分七个阶段 |
| 范围 | ADR-0013 + ADR-0014 | ADR-0004 + ADR-0013 + ADR-0015 全部, ADR-0014 只做非沙箱条款 |
| 沙箱 | SRT Provider, `/sandbox`, 实例生命周期 | **不实现**, 后续可能不再依赖 SRT |
| 工具系统 | "不重做通用 Tool Registry" | 按 ADR-0004 细化版重做了契约, 注册表与运行时 |

### 10.2 七个实现阶段

1. ADR-0004 契约冻结: 能力闭集词汇, `ToolSpec` / `ToolPlan` / 授权信封 / 结果归一化,
   `ToolRegistry`, 协调器骨架, mode 能力门, 依赖方向静态检查并入 `make ci`.
2. 执行机制: `ToolRuntime` 强制授权前置, `ResourceGovernor`, `CommandExecutor`,
   执行画像与环境净化, 五个只读内置工具.
3. Shell 解析: POSIX / cmd / PowerShell 三方言, 包装器递归, heredoc, 受控目标展开.
4. 规则引擎: Hard Deny 与预扫描, mode 能力矩阵, 可执行文件身份, 受保护路径,
   受限学习规则, `/add-dir` 读写分级.
5. 脚本分析与分类器: 确定性 `ScriptAnalyzer`, `SafetyClassifier` 与 fail-safe,
   内容哈希缓存, 经现有 LLM 网关的分类器 adapter.
6. 恢复层: 首次破坏性写入屏障, 内容寻址 RecoveryStore, 冲突检查, 崩溃恢复.
7. 端到端接线: 协调器串起三层, 审计事件, 红蓝语料, CLI 命令.

### 10.3 缓期项

- 沙箱层整体 (ADR-0014 的 SRT Provider, `/sandbox`, 实例生命周期, 临时可写层).
- ADR-0015 的 `OVERLAY` 快照策略 (依赖沙箱的临时写层).
- ADR-0013 §9 中 `STRONG_SANDBOX` / `PARTIAL_SANDBOX` 的 `auto` 分支.
- MCP 工具接入 (契约已按 ADR-0004 §11 预留: MCP 必须声明 untrusted + opaque).
- `BuiltinAgentLoop` 尚未从模型 tool_calls 产出 `ToolRequestAction`; 分发路径已经打通,
  接上 gateway 的 tool calling 即可.
