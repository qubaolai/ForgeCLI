# ADR-0050：采用显式状态机、Effect 执行器与有序 Stage 重构 Agent 主循环

## 状态

Superseded by ADR-0049 (2026-09-11)

本文的内容已并入 ADR-0049: 循环状态改枚举, 先写行为基线, Skill 与子 Agent 的边界, 安全管线不做成插件这几条被采纳; 纯 reducer 内核, Effect 执行器, Handler 注册表, `ReplaceState` 与数字顺序不采纳, 理由见 ADR-0049 备选方案 A 与 G。子 Agent 的运行身份, 审批归属, 取消传播, 预算归属四件事推迟到单独的 ADR。

## 日期

2026-09-11

## 背景

ForgeCLI 当前采用受控 ReAct 作为 Agent 主循环。这个方向仍然成立：模型只产生结构化意图，
真实工具执行必须经过安全管线，事件日志与会话状态由 ForgeCLI 自己掌握，不能交给第三方
Agent 框架作为唯一事实源。

但随着工具、安全、审批、上下文压缩、工作区监测、计划、记忆和 Web 运行事件逐步接入，
循环的控制流正在成为多个能力的汇合点。每个能力单独看都有必要，合在一起后却出现了以下
维护问题：

1. 新增一个循环行为通常需要修改中心循环、外层 Turn 驱动、动作类型、停止结果和事件映射
   多个位置。
2. 循环内部通过多个布尔值、队列和计数器隐式表达状态，非法状态组合无法在类型层面被发现。
3. LLM 调用、上下文压缩、工作区变化和进度保护等横切能力虽然已经拆成协作者，但仍由
   `BuiltinAgentLoop` 直接编排，拆分没有形成真正的扩展协议。
4. `BuiltinAgentLoop` 与 `AgentTurnService` 各自拥有一部分循环控制流，导致“循环内核”和
   “动作执行驱动”边界不够清楚。
5. 未来接入 Skill、Sub-Agent 和 Multi-Agent 时，如果继续为每种能力添加 `if/elif` 分支，
   主循环会从 ReAct 循环演化为包含调度、权限、并发和子任务管理的手写工作流引擎。

### 当前运行链路

当前一轮请求的大致过程如下：

```text
ProjectRuntime.start_turn
  -> AgentTurnService.handle_user_message
      -> 记录 user_message
      -> 组装本轮 Context 与 ToolCatalog
      -> BuiltinAgentLoop.start
          -> 检查工作区变化
          -> 检查模型预算
          -> 计算上下文压缩
          -> 构造 ModelRequest
          -> 调用 LlmGateway
          -> 校验模型输出
          -> 产生 AnswerAction 或 ToolRequestAction
      -> AgentTurnService 解释动作
          -> CoordinatorToolDispatcher
              -> ToolRequestCoordinator
                  -> describe / prepare / evaluate / approval / recovery / execute
          -> 将 ToolObservation 回填 BuiltinAgentLoop.observe
      -> 循环继续或停止
      -> 记录 assistant、usage、compaction 和窗口
```

当前代码中的主要控制点：

- [`BuiltinAgentLoop._advance()`](../../src/forgecli/application/agent_loop/builtin_loop.py) 同时
  处理预算、上下文、模型调用、模型异常、工具校验、工作区变化和动作选择。
- [`BuiltinAgentLoop._observe_tool()`](../../src/forgecli/application/agent_loop/builtin_loop.py) 同时
  处理 observation 回填、transcript、进度保护、计划评审、工具关闭和下一步推进。
- [`AgentTurnService._run_loop()`](../../src/forgecli/application/agent_turn/agent_turn_service.py)
  通过 `for` 循环和 `isinstance(action)` 再实现一层动作驱动。
- [`ToolRequestCoordinator`](../../src/forgecli/application/tool_request/coordinator.py) 仍然是
  工具安全与执行的唯一管线；本 ADR 不改变这条安全边界。

截至本 ADR，`BuiltinAgentLoop` 约 848 行，`AgentTurnService` 约 737 行。前者虽然已经拆出
`AgentModelInvoker`、`ProgressGuard`、`LoopEventPublisher` 和 transcript 函数，但主循环仍然
直接持有以下状态：

```text
started / finished
pending_calls / dispatched
tools_closed
malformed_responses / overflow_compactions
model_calls / tool_calls
window / workspace_changes
usage_drafts / compaction_drafts
```

这些字段共同表示一个隐式状态机。例如，`_dispatched is None` 可能表示还没有工具、工具已经
完成、答案已经产生或循环即将停止；`_tools_closed` 又与 `_tools`、`_pending_calls` 共同决定
是否还能接受模型工具调用。

### 设计约束

本次重构不能破坏已有约束：

- AgentLoop 不能直接执行工具、shell、文件写入或长期记忆。
- 所有工具调用必须经过 `ToolRequestCoordinator`。
- 安全策略、审批、恢复、围栏和执行前状态复核仍由现有工具管线负责。
- `events.jsonl + state.json` 仍是恢复事实源，循环内存状态不能替代它们。
- 事件订阅者只读，不通过事件返回控制信号。
- 模型输出、工具输出和 Skill 内容均属于不可信输入，不能改变安全策略。
- 工具调用默认继续保持串行；并行能力只在明确的冲突域和预算模型建立后引入。

## 决策

采用“显式状态机 + Effect 执行器 + 有序 Stage Pipeline”的 Agent Runtime 结构。

核心原则是：

> Agent 主循环只负责根据输入事件进行状态转换，并产生类型化 Effect；所有外部副作用由
> 运行时执行器完成；可插拔行为通过有序 Stage 或 EffectHandler 接入，而不是直接修改中心
> 循环。

本决策不是引入新的第三方 Agent 框架，也不是立即重写当前 `BuiltinAgentLoop`。它定义后续
重构的目标结构和边界，采用渐进迁移方式落地。

### 决策 1：用显式 LoopState 表达循环状态

循环状态应从分散的布尔值、队列和计数器中收敛为一个明确的 `LoopState`。它是运行时状态，
不是持久化事实源。

目标形状如下：

```text
LoopState
  identity
    session_id
    turn_id
    run_id
    agent_id
    parent_agent_id?

  phase
    INITIALIZING
    WAITING_MODEL
    MODEL_RESULT_READY
    WAITING_ACTION
    WAITING_TOOL
    WAITING_CHILD_AGENTS
    WAITING_USER
    COMPLETED
    STOPPED

  context
    assembled_context
    window
    tool_catalog
    capability_snapshot

  execution
    pending_effects
    in_flight_effects
    completed_effects

  governance
    budget_state
    retry_state
    progress_state
    cancellation_state

  outputs
    answer?
    usage_drafts
    compaction_drafts
```

状态转换必须有明确的输入和输出：

```text
LoopEvent + LoopState -> LoopTransition

LoopTransition
  next_state
  effects[]
  emitted_events[]
  stop?
```

`LoopState` 可以是不可变值对象，也可以由一个受控的 reducer 更新；具体实现以测试便利性
为准，但不能让外部协作者直接持有并任意修改内部字段。

### 决策 2：采用事件驱动的 Reducer / State Machine 作为循环内核

循环内核不再通过递归调用 `_advance()` 隐式推进，而是使用事件驱动的状态转换：

```text
UserTurnStarted
  -> CallModel Effect

ModelCompleted
  -> DispatchTool / SpawnSubAgent / EmitAnswer / Pause / Stop

ToolCompleted
  -> DispatchNextEffect / CallModel Effect / Pause / Stop

SubAgentCompleted
  -> MergeReport / CallModel Effect / Pause / Stop

CancelRequested
  -> Cancel in-flight effects / Stop
```

循环运行器的职责收敛为：

```text
while state is not terminal:
    transition = machine.reduce(event, state)
    publish(transition.emitted_events)
    effects = transition.effects
    results = executor.execute(effects)
    event = map_results_to_event(results)
```

这段循环仍然存在，但它不包含工具、审批、上下文压缩和模型异常的业务分支。它只是把
状态转换和 Effect 执行结果连接起来。

以下行为必须显式建模，不能再使用 `None` 表示：

- 纯反思或无动作：使用 `Noop` 或 `ContinueReasoning` 事件。
- 答案已生成但尚未由外层接收：使用 `AnswerProduced` 和 `AnswerDelivered`。
- 工具已排队但没有执行：使用 `EffectAbandoned`。
- 子 Agent 正在运行：使用 `ChildRunStarted` 和 `ChildRunCompleted`。

### 决策 3：外部副作用统一表示为 Effect

定义稳定的 Effect 类型空间。第一阶段至少包括：

```text
CallModel
DispatchTool
EmitAnswer
RequestHumanDecision
SpawnSubAgent
JoinChildAgents
CompactContext
StopLoop
```

Effect 只描述意图，不携带可绕过安全管线的执行对象。例如：

```text
DispatchTool
  tool_request: ToolRequest

SpawnSubAgent
  task
  context_slice
  capability_scope
  budget_scope
  output_schema
```

Effect 的执行由 `EffectExecutor` 完成：

```text
EffectExecutor
  CallModel       -> ModelInvoker / LlmGateway
  DispatchTool    -> CoordinatorToolDispatcher
  EmitAnswer      -> Turn result collector
  RequestHuman... -> HumanInteraction channel
  SpawnSubAgent   -> SubAgentRunner
  CompactContext  -> WindowManager
```

`DispatchTool` 必须继续走 `CoordinatorToolDispatcher -> ToolRequestCoordinator`，不能因为
引入 Effect 执行器而把安全管线下沉到循环内部。

### 决策 4：使用有序 Stage Pipeline 承载循环介入点

Stage 是受约束的生命周期介入点，解决“增加或删除循环行为必须修改中心方法”的问题。

Stage 的目标契约如下：

```text
LoopStage
  name
  order
  applies(runtime_context) -> bool
  before(input, state) -> StageResult
  after(output, state) -> StageResult
```

`StageResult` 只允许以下几种结果：

```text
Continue
ReplaceState(LoopState)
EmitEffect(Effect)
Stop(LoopStop)
Pause(LoopStop)
```

Stage 不允许：

- 直接访问 `ToolRegistry`、`ToolRuntime` 或具体文件系统。
- 直接写 Session、EventStore 或长期 Memory。
- 直接修改另一个 Stage 的私有状态。
- 通过字符串约定改变控制流。
- 在未声明的情况下改变工具目录、权限、预算或审批结果。

初始 Stage 顺序固定为：

```text
10  InputStage
20  WorkspaceCheckpointStage
30  BudgetGuardStage
40  ContextFitStage
50  ModelRequestStage
60  ModelInvocationStage
70  ModelOutputNormalizeStage
80  ActionSelectionStage
90  EffectPolicyStage
100 EffectExecutionStage
110 ObservationNormalizeStage
120 TranscriptStage
130 ProgressStage
140 TerminationStage
```

Stage 的顺序不是普通插件可以自由重排的配置项。涉及安全、授权、恢复和预算的 Stage 必须
由核心注册表固定顺序；新增 Stage 只能插入已声明的生命周期槽位，并通过依赖关系校验。

### 决策 5：把可扩展动作交给 EffectHandlerRegistry

外层驱动不再通过一组不断增长的 `isinstance` 分支解释动作，而是按 Effect 类型寻找处理器：

```text
EffectHandlerRegistry
  CallModel       -> ModelEffectHandler
  DispatchTool    -> ToolEffectHandler
  SpawnSubAgent   -> SubAgentEffectHandler
  EmitAnswer      -> AnswerEffectHandler
  RequestHuman... -> HumanInteractionEffectHandler
```

新增一种 Effect 的最低变更范围为：

1. 新增 Effect 值对象。
2. 新增对应 Handler。
3. 在组合根注册 Handler。
4. 增加状态转换和 Handler 的契约测试。

核心 Runner 不应因为增加 Skill、Sub-Agent 或新的人工交互类型而增加一条业务分支。

### 决策 6：事件与控制信号严格分离

继续保留当前事件总线的只读语义：

```text
LoopEvent -> EventBus -> renderer / trace / usage / diagnostics
```

事件订阅者不能返回 `Continue`、`Stop` 或修改后的请求。需要改变循环方向的能力必须通过
Stage、Reducer 或 EffectHandler 接入。

运行事件应逐步补充以下身份字段，以支持子任务和未来多 Agent：

```text
session_id
turn_id
run_id
agent_id
parent_run_id?
parent_agent_id?
invocation_id?
request_id?
```

`agent_id` 和 `parent_run_id` 是事件身份的一部分，不从日志上下文推导。父 Agent 与子 Agent
的事件可以共用事件总线，但必须能按 run、agent 和 parent 关系过滤，不得混成一条无法恢复的
全局时间线。

### 决策 7：保留当前工具安全管线，不把安全规则做成普通插件

Stage 只负责循环层面的治理，例如预算检查、上下文适配和重试判定；以下规则仍归现有安全
与工具模块：

```text
ToolCatalog filtering
Tool.prepare
Authorization decision
Approval binding
Execution environment validation
Recovery checkpoint
ToolRuntime.execute
Tool observation normalization
```

普通 Skill 或第三方 Agent 框架不能注册一个 Stage 来跳过这些步骤。若 Stage 的输出是
`DispatchTool`，仍必须由统一工具协调器处理。

### 决策 8：Skill 作为能力快照和上下文提供者接入

Skill 不作为主循环中的特殊 `if skill_name` 分支，也不直接成为一个拥有额外权限的 Agent。

Skill 的接入路径为：

```text
SkillManifest
  -> SkillLoader
  -> SkillTriggerResolver
  -> SkillCapabilityCompiler
  -> TurnCapabilitySnapshot / ContextProvider / ToolFilter
  -> LoopInput
```

Skill 可以提供：

- 任务触发条件。
- 结构化指令或上下文片段。
- 输出 schema。
- 工具建议或受限工具过滤器。
- Skill 自身的版本和内容指纹。

Skill 不可以提供：

- 绕过 `ToolRequestCoordinator` 的执行入口。
- 覆盖全局安全策略、模式权限或审批结论。
- 直接修改 Session 状态。
- 直接写入工作区或长期 Memory。

Skill 内容应在本轮开始时编译为不可变快照。Skill 运行期间如果文件发生变化，不自动改变
本轮已经发送给模型的系统提示词和工具目录；下一轮重新解析并应用新版本。

### 决策 9：Sub-Agent 复用同一 Loop Contract，通过独立 Effect 启动

Sub-Agent 不是主循环中的另一套特殊循环，而是一个受限的子运行：

```text
Root Loop
  -> SpawnSubAgent Effect
  -> SubAgentRunner
      -> 创建 Child LoopState
      -> 运行同一 LoopMachine / Stage Pipeline
      -> 通过受限 EffectExecutor 执行
      -> 产出 AgentReport
  -> SubAgentCompleted event
  -> ParentObservation
  -> Root Loop 继续
```

Sub-Agent 输入至少包括：

```text
task
context_slice
capability_scope
budget_scope
output_schema
parent_run_id
```

Sub-Agent 输出优先采用结构化报告：

```text
summary
findings
evidence
recommended_actions
confidence
```

第一阶段的 Sub-Agent 默认无写权限，只读工具和只读上下文。需要修改时返回建议，由父 Agent
重新产生 `DispatchTool`，再次经过主工具安全管线。

### 决策 10：Multi-Agent 先采用层级式调度，不引入任意 Agent 对话

第一阶段只支持一个 Root Agent 派发有限数量的子 Agent：

```text
Root Agent
  ├── Research Agent
  ├── Reviewer Agent
  └── Debug Agent
```

并发调度属于 `SubAgentScheduler` 或 `AgentProcessManager`，不属于 LoopMachine。主循环只
关心 `Spawn -> Wait/Join -> Observe`，不关心线程、进程或远端任务实现。

调度器必须管理：

- 最大子 Agent 数量。
- 子 Agent 独立 token、时间和成本预算。
- 父子任务取消传播。
- 子任务超时和结果未知。
- 只读任务并发限制。
- 写路径或动态目标之间的冲突隔离。
- 父子运行事件和 tracing 关系。

在没有明确冲突域、租约和恢复语义之前，不允许多个 Agent 共享可变工作区写权限。

## 目标架构

重构后的目标依赖关系如下：

```text
AgentTurnService
  -> TurnRunner
      -> LoopMachine
          -> LoopState
          -> StagePipeline
          -> EffectFactory
      -> EffectExecutor
          -> ModelEffectHandler
          -> ToolEffectHandler
          -> HumanInteractionEffectHandler
          -> SubAgentEffectHandler
      -> Session / Event / Result sinks

ToolEffectHandler
  -> CoordinatorToolDispatcher
      -> ToolRequestCoordinator

SubAgentEffectHandler
  -> SubAgentRunner
      -> child TurnRunner
```

其中：

- `LoopMachine` 是控制流内核。
- `StagePipeline` 是可扩展的循环介入机制。
- `EffectExecutor` 是副作用边界。
- `ToolRequestCoordinator` 仍是工具安全事实链的唯一入口。
- `TurnRunner` 负责把状态转换、Effect 执行结果和外部事件连接起来。
- Session、事件落盘、usage 和最终响应仍由外层应用服务统一收口。

## 迁移方案

采用行为不变、逐层替换的迁移方式，不进行一次性重写。

### 阶段 0：建立行为基线

补齐最小契约测试和场景测试，至少覆盖：

- 纯文本回答。
- 单工具调用和多工具串行队列。
- 工具被拒绝、审批等待和用户取消。
- 重复调用、连续无新信息和模型格式错误重试。
- 上下文压缩和供应商上下文溢出。
- 工作区外部变化与 Agent 工具变化。
- 流式回答中断。
- 计划评审暂停与恢复。

测试应使用内存模型、工具和事件订阅者替身，但保留真实的动作契约和工具安全协调器。

### 阶段 1：抽取 LoopState，不改变外部接口

保留 `BuiltinAgentLoop.start()` / `observe()` 作为兼容外观，将现有字段收敛到 `LoopState`。
先把隐式状态转换写成显式方法，并为每个 phase 增加合法转换断言。

这一阶段不引入 Skill 或 Sub-Agent，目标是先消除状态组合不透明的问题。

### 阶段 2：将 `_advance()` 和 `_observe_tool()` 改造成 Stage Pipeline

按以下顺序迁移：

1. ContextFitStage。
2. ModelInvocationStage。
3. ModelOutputNormalizeStage。
4. ActionSelectionStage。
5. ObservationNormalizeStage。
6. ProgressStage。
7. TerminationStage。

每迁移一个 Stage，都保留旧路径与新路径的行为对照测试。`LoopEventPublisher`、
`AgentModelInvoker` 和 `ProgressGuard` 可以作为现有 Stage 的内部实现继续复用。

### 阶段 3：引入 EffectExecutor 和 HandlerRegistry

先把当前两种动作映射为：

```text
AnswerAction      -> EmitAnswer
ToolRequestAction -> DispatchTool
```

然后把 `AgentTurnService._run_loop()` 中的动作分支迁移到 HandlerRegistry。完成后，
`AgentTurnService` 不再因新增动作类型而扩展中心 `if/elif`。

### 阶段 4：接入 Skill

先实现只读 Skill：

- manifest 加载。
- 触发匹配。
- 指令和输出 schema 编译。
- 工具过滤。
- 版本指纹。

Skill 的结果只进入 `TurnCapabilitySnapshot` 和上下文编译，不直接改变安全管线。

### 阶段 5：接入只读 Sub-Agent

实现 `ResearchAgent` 或 `ReviewerAgent` 作为第一个子 Agent：

- 通过 `SpawnSubAgent` Effect 启动。
- 使用独立 LoopState。
- 只开放只读工具。
- 返回结构化报告。
- 事件带 `agent_id` 和 `parent_run_id`。

在此阶段不允许子 Agent 直接写文件，也不实现任意 Agent 互相对话。

### 阶段 6：增加有限并发和 Multi-Agent 调度

只有在以下事实具备后才实施：

- 工具 Effect 已有冲突域或等价目标集合。
- 子 Agent 有独立预算和取消传播。
- 父子事件可恢复和过滤。
- 结果未知时不会自动重放副作用。
- 共享 Memory 和工作区访问权限已有明确边界。

## 备选方案

### 方案 A：继续在 BuiltinAgentLoop 中增加分支

优点：改动最小，短期容易实现。

缺点：每新增一种能力都要修改中心循环、状态字段、动作转换和测试；Skill、Sub-Agent、
审批和并发会进一步扩大耦合；难以验证某个功能删除后没有残留状态。

不采用。

### 方案 B：只使用通用 LoopHook

优点：概念简单，可以在模型前后插入逻辑。

缺点：通用 Hook 往往需要任意修改 state/request，类型边界弱；Hook 的顺序、覆盖关系和
错误处理容易变得不可预测；复杂 Hook 最终会重新实现一套隐式状态机。

不作为主方案。保留少量、类型明确的 Stage 介入点，避免开放式 Hook 修改任意状态。

### 方案 C：用事件订阅者驱动控制流

优点：解耦生产者和消费者，新增功能无需修改主循环。

缺点：事件是异步或只读观察，无法安全表达“必须先审批”“必须暂停”“必须重新裁决”等
控制要求；订阅者异常、顺序和重复消费会让循环结果不可预测。

不采用。事件总线继续只负责观察、渲染、追踪和审计。

### 方案 D：直接引入 LangGraph、AutoGen 等高层 Agent Runtime

优点：可以快速获得状态图、并发和多 Agent 编排能力。

缺点：第三方状态、checkpoint、消息和工具模型容易与 ForgeCLI 的会话、权限、恢复和
事件模型重复；迁移后很难保证所有路径都经过本地安全协调器。

不作为当前主路径。若未来接入第三方框架，只允许它实现 `LoopMachine` 或某个受限的
Sub-Agent Runner，并通过 ForgeCLI 的 State、Effect、Event 和 Tool Contract 适配。

### 方案 E：直接采用完整的 Actor / 消息系统

优点：天然适合多 Agent、邮箱和并发任务。

缺点：会提前引入消息投递、持久化、重试、顺序、租约和分布式一致性成本；当前主要问题
是单 turn 控制流耦合，并非必须先解决分布式调度。

不采用为当前阶段方案。未来 Multi-Agent 真正需要长期后台任务或跨进程协作时，再独立评估
Actor 或 Process Manager。

## 影响

### 收益

- 主循环从“所有能力的编排中心”收敛为状态转换内核。
- 新增循环行为可以通过 Stage 注册完成，减少中心方法修改。
- 新增动作只需增加 Effect 和 Handler，不需要扩展 `AgentTurnService` 的分支。
- Skill、Sub-Agent 和未来 Multi-Agent 共享同一套生命周期、预算、取消和事件模型。
- 显式 phase 能够识别非法状态，暂停、恢复和取消语义更清晰。
- 事件、工具安全管线和会话持久化继续由 ForgeCLI 自己掌握，不被第三方框架替代。
- 子 Agent 的并发和调度不会污染 Root Agent 的核心控制流。

### 代价

- 会增加 `LoopState`、`LoopEvent`、`LoopTransition`、Effect 和 Handler 等契约类型。
- 调试时需要同时查看状态转换和 Effect 执行记录。
- Stage 顺序、版本和依赖关系需要额外测试。
- 子 Agent 会增加预算、取消、事件身份和结果未知等治理成本。
- 迁移期间需要短暂保留兼容外观和新旧路径对照测试。

### 兼容性

- 现有 `LoopInput`、`LoopObservation`、`LoopStopReason` 可以在第一阶段继续保留。
- 现有 `ToolRequestCoordinator`、`WindowManager`、`AgentModelInvoker` 和事件总线不要求立即
  重写。
- 现有 `events.jsonl` 和 `state.json` 仍为恢复来源，不引入第三方 checkpoint 作为替代。
- 新增 `agent_id`、`parent_run_id` 等事件字段时，应提供旧事件读取兼容策略；无法可靠归属
  的旧事件不能静默归入当前 Agent。

### 后续约束

- 新增 Stage 必须说明生命周期位置、输入输出类型、是否能 Stop/Pause，以及与既有 Stage 的
  顺序关系。
- 新增 Effect 必须说明副作用、权限边界、取消语义、预算归属、事件和恢复行为。
- Stage 不能直接执行副作用；EffectHandler 不能绕过现有安全管线。
- Skill 不能通过 manifest 获得额外安全权限。
- Sub-Agent 默认无写权限；任何写权限都必须由父级任务显式授予并经过同一安全管线。
- 不以文件行数作为拆分目标；拆分必须能够减少控制流耦合或形成可验证的扩展边界。
- 不因为“未来可能需要”提前增加没有生产方和消费方的字段、接口或空实现。

## 验收标准

### 控制流与状态

- `LoopState.phase` 能覆盖初始化、等待模型、等待动作、等待工具、等待子 Agent、等待用户、
  完成和停止状态。
- 每个 phase 的合法输入事件和输出 Effect 有契约测试。
- 不再用 `None` 表示不同语义的循环状态。
- 取消、暂停、完成和失败各自只有一个终态。

### 扩展性

- 新增一个不涉及安全的 Stage，不修改核心 Runner 的控制流分支。
- 新增一个 EffectHandler，不修改 `AgentTurnService` 的动作解释分支。
- 删除一个 Stage 或 Handler 后，不残留中心循环状态字段。
- Stage 顺序确定且可审计，重复或非法顺序能在装配期失败。

### 安全与副作用

- 所有 `DispatchTool` 仍经过 `CoordinatorToolDispatcher` 和 `ToolRequestCoordinator`。
- Stage、Skill 和 Sub-Agent 不能直接访问工具运行时或写入工作区。
- 审批、恢复、围栏和执行前状态复核行为与重构前一致。
- 工具、Skill 和 Sub-Agent 输出不会改变工具目录、安全策略或授权结果。

### Skill 与 Sub-Agent

- Skill 在一轮开始时形成不可变能力快照，版本变化在下一轮生效。
- Skill 只能影响上下文、输出 schema 和工具建议/过滤，不能提高权限。
- Sub-Agent 使用独立 LoopState、预算和事件身份。
- 只读 Sub-Agent 能返回结构化报告，父 Agent 能将报告作为 observation 继续运行。
- Sub-Agent 无直接写权限，写操作必须由父 Agent 重新发起并经过统一工具管线。

### 运行质量

- 现有纯回答、工具调用、审批、取消、压缩、工作区变化、流式中断和计划评审场景行为不退化。
- 运行事件能够区分 `session_id`、`turn_id`、`run_id` 和 `agent_id`。
- 运行事件仍然只读，订阅者异常不会改变循环结果。
- 完成迁移后，当前工作区的架构检查、依赖检查、类型检查和完整测试通过。

## 关联文档

- [`ADR-0010：采用受控 ReAct 作为 Agent 主循环架构`](2026-07-01-0010-采用受控ReAct作为Agent主循环架构.md)
- [`ADR-0016：采用统一 Agent 运行事件流驱动终端过程展示`](2026-08-03-0016-采用统一Agent运行事件流驱动终端过程展示.md)
- [`ADR-0028：采用轻量 DDD 结构收敛判据`](2026-08-21-0028-采用轻量DDD结构收敛判据.md)
- [`ADR-0041：采用按变更源分层的请求组装与只追加窗口`](2026-09-02-0041-采用按变更源分层的请求组装与只追加窗口.md)
- [`ADR-0043：采用统一人机提示通道与 ask_user 工具`](2026-09-03-0043-采用统一人机提示通道与ask-user工具.md)
- [`ADR-0048：采用运行状态一致性与模块职责收敛方案`](2026-09-06-0048-采用运行状态一致性与模块职责收敛方案.md)
- [`02-detailed-design.md`：Skills、Sub-Agent 与 Multi-Agent 设计](../02-detailed-design.md)
- [`application/agent_loop`](../../src/forgecli/application/agent_loop/)
- [`application/agent_turn/agent_turn_service.py`](../../src/forgecli/application/agent_turn/agent_turn_service.py)
