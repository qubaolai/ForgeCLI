# 代码阅读路线

给第一次通读这个代码库的人. 目标不是"读完所有文件", 而是**能独立回答"这次工具调用为什么
被拦下来了"**这类问题.

## 0. 这份文档怎么用

代码规模 (2026-08-27):

| 层 | 文件 | 行数 | 说明 |
|---|---|---|---|
| `shared` | 10 | 1058 | 取消令牌, JSON Schema 校验, 结构化日志与进程内读数 (ADR-0035) |
| `domain` | 91 | 9678 | 纯值对象, 不读文件不起进程 |
| `application` | 142 | 20221 | 用例编排与 ABC 端口 |
| `infrastructure` | 52 | 4916 | 适配器 |
| `interfaces` | 60 | 8753 | CLI, 本地 Web, 共享组合根 |
| `tests` | 91 | 17449 | 854 个用例 |
| `web/src` | 7 | 2335 | React 控制面前端 (ADR-0025) |

**不要按目录顺序读.** 四万行按字母序读完也建立不起图景. 正确的方式是**跟着一次真实请求
走完整条链路**, 中途遇到不懂的值对象再回头翻.

路线分三段:

1. **建立骨架** (约半天): 四份材料, 读完知道"东西大致在哪".
2. **走通三条主线** (约三天): 跟着请求走完三条链路. 这是主体.
3. **按需深入支线**: 用到哪块看哪块.

每一站都给出: 读什么文件 / 配套哪份 ADR / **读完应该能回答什么问题**. 最后一项是自检 ——
答不上来说明这一站没读透, 往下走会越来越吃力.

### 0.1 让运行日志替你走一遍

"跟着一次真实请求走"可以不只是比喻. 把配置项"日志级别"调成 `debug` (`/config` ->
常规配置), 重启后跑一句话, 然后读那份日志 (ADR-0035):

```bash
poetry run forge cli
```

```bash
tail -f ~/.forge/logs/forge-latest.log
```

一次带工具调用的对话会按发生顺序留下 `turn.received` -> `prompt.compiled` ->
`context.fit` -> `model.request` -> `model.response` -> `tool.requested` ->
`pipeline.start` -> `pipeline.prepared` -> `pipeline.decision` -> `tool.execute.ok` ->
`tool.observed` -> `loop.stop`. 每一行的模块路径就是文件路径 (去掉 `forgecli.` 前缀),
照着 open 即可.

比通读快的地方在于**顺序是真的**: 文档里的链路图是作者整理过的, 日志里的是这台机器上
刚刚实际发生的, 包括那些文档没写的分支 (重试, 降级, 被闸拦下的调用).

几个常用开关都在 `/config` 的"常规配置"里: "日志同时写终端"把日志也打到 stderr,
"日志包含 HTTP 库"把 httpx / uvicorn 的记录收进同一个文件. 会话内敲 `/diagnostics`
能看到日志路径与本进程的计数与耗时读数 —— 后者要先打开"运行指标采集".

## 1. 建立骨架

按顺序读这四份, 不要跳:

### 1.1 `scripts/check_arch.py` (312 行) 与它的两个同伴

**第一个读它, 而不是任何架构文档.** 它是这个项目唯一一份**可执行的**架构说明: 四组规则
写成断言, 违反就在 `make ci` 停下.

四组规则分别回答:

- `LAYER_BANS`: 谁不许 import 谁 (domain 不 import 任何内部层; application 不 import
  infrastructure 与 interfaces; infrastructure 不 import interfaces).
- `BANNED_THIRD_PARTY`: domain 与 application 不许碰 rich, prompt_toolkit, httpx,
  requests, openai, anthropic —— 这条决定了"业务逻辑长什么样".
- `SIBLING_BANS`: 14 条同层互斥. 工具层与安全层互相不可见; 人工 Shell 与工具 / 安全 /
  循环互相不可见; `agent_loop` 与 `context` 只认识 `LlmGateway` 这个 ABC, 不认识
  `DefaultLlmGateway`; `memory` 不认识 `security`; `tool_request` 不认识事件总线与
  会话写入口.
- **无 import 环**: 模块级 import 图必须是有向无环的. 这条是 2026-08-24 补的 ——
  `application/llm/config` 与 `application/llm/gateway` 的包门面各自藏过一个环, 都是
  靠 import 顺序活着的.

`make arch` 还跑另外两个脚本, 一起读:

- `scripts/check_abstractions.py` (121 行, ADR-0028): 一个 ABC / Protocol 只在满足
  A1 依赖倒置, A2 多实现, A3 开放扩展点三条之一时才保留. `AgentLoop` 与 `ToolDispatcher`
  这两个抽象就是按这条判据删掉的 —— 只有一个实现, 且实现与抽象同层.
- `scripts/check_prompt_text.py` (117 行, ADR-0031 / ADR-0039): 送进模型上下文的参数里
  不得出现含中文的字符串字面量. 正文一律从 `application/prompt/templates/` 渲染.

**读完能回答**: 为什么 `application/tools` 里没有任何 `import ...security`?
为什么 `AgentLoop` 这个 ABC 被删了, 而 `Tool` 这个 ABC 留着?

### 1.2 `AGENTS.md` + `docs/01-overview-design.md` §5.1

命名约定, 分层职责, 禁用名 (`AgentWorkflow` / `BuiltinWorkflow` / `WorkflowResult`, 见
已废弃的 ADR-0003).

### 1.3 `docs/adr/README.md`

40 份 ADR (0000-0039) 的索引与状态. **不要现在全读**, 只记住有这么一批东西, 以及哪几份
已经不作数:

| ADR | 状态 |
|---|---|
| 0003 | Discarded, 由 ADR-0010 取代 |
| 0009 | Discarded, `ModePolicy` 那套口径不再成立 |
| 0007 | Superseded by ADR-0025 (Web 成为主入口) |
| 0017 | Superseded by ADR-0025 (Web 不提供人工终端; `forge cli` 下 `#` 仍在) |
| 0019 | Superseded by ADR-0030 |
| 0020 | Partially Superseded by ADR-0030 (LLM 安全分类器整体删除) |
| 0024 | Superseded by ADR-0030 |
| 0014 | 保留沙箱生命周期与自测条款, Provider 选型先后由 0019, 0030 取代 |

**ADR-0030 是一次大改**: 它删掉了 LLM 安全分类器, 风险缓存与静态命令证明, 改由运行期
围栏 (Seatbelt / bubblewrap / WSL2) 与工作区快照兜底. 读到任何提"分类器"的旧材料时,
先确认它是不是在 0030 之前写的.

### 1.4 `src/forgecli` 的目录树

```text
shared/          不属于任何一层的基础件: 取消令牌, JSON Schema, 日志与读数
domain/          纯值对象. 认识这里的名字就认识了整个系统的词汇
  agent/         循环动作, 循环输入, 停止原因, 28 种运行事件
  tool/          ToolSpec / ToolPlan / Capability / ExecutionAuthorization
  security/      安全词汇, Hard Deny, 受保护路径, 能力预算 + Shell 解析 (shell/ 子包)
  execution/     执行画像, 环境净化, FencePolicy (围栏边界)
  context/       上下文预算与压缩结果 (ADR-0032)
  memory/        记忆条目与疑似凭证识别 (ADR-0033)
  planning/      Plan 与 TodoList (ADR-0022)
  prompt/        PromptBlock / PromptSnapshot
  recovery/      恢复检查点与变更集
  conversation/  ChatMessage 与 turn
  session/       会话事件与快照
  workspace/     工作区边界与路径归一
  model/         LLM 请求 / 响应 / 流式
  config/        配置键与合并结果
application/     用例编排. ABC 端口也在这里
  agent_loop/    ReAct 循环 (BuiltinAgentLoop)
  agent_turn/    唯一写文件, 起子进程的驱动方
  agent_run/     运行事件总线与观察者 (ADR-0016)
  tool_request/  工具请求协调器 (安全管线的编排者) + 审批流 + 恢复流 + 调度器
  tools/         工具机制层 + builtin/ 十四个内置工具
  security/      安全策略层 (分析器, 策略引擎, 审批, 学习规则)
  context/       上下文压缩与回合内去重 (ADR-0032)
  memory/        跨会话记忆 (ADR-0033)
  planning/      计划 / 待办服务与计划评审 (ADR-0022, ADR-0023, ADR-0038)
  recovery/      恢复事务
  prompt/        系统提示词编译 + templates/ 模型可读正文
  llm/           LLM 网关与模型目录
  manual_shell/  人工 Shell (ADR-0017)
  slash_commands/ 斜杠命令注册表
  workspace/     ExecutionContext 与文件系统视图
  project/       项目身份与工作区信任
  session/       会话服务与 resume
  config/        配置服务
infrastructure/  适配器: 文件系统, 子进程, 沙箱 Provider, JSON, HTTP, LLM adapters
interfaces/
  cli/           Typer 入口, REPL, 终端渲染, 斜杠命令, TTY 适配
  web/           FastAPI 应用, SSE 事件流, 审批 broker, 内嵌静态资源
  runtime/       CLI 与 Web **共用**的组合根: tool_wiring / llm_wiring / logging_wiring
```

**读完能回答**: `ToolPlan` 应该在哪一层? 为什么?

## 2. 主线一: 一次对话是怎么跑完的

从用户敲下一行字, 到终端出现回复. 这条线最短, 先走它建立"请求在系统里怎么流动"的感觉.

走 `forge cli` 这条路径读, 因为它最短; Web 那条走的是同一套 application, 只换了适配器
(见 §5).

| # | 文件 | 看什么 | 读完能回答 |
|---|---|---|---|
| 1 | `interfaces/cli/app.py` (107 行) | Typer 入口, 两条启动路径 | 裸 `forge` 起 Web 而 `forge cli` 进终端, 为什么这两条不能同时对同一项目跑? |
| 2 | `interfaces/cli/bootstrap.py::run()` (387 行) | CLI 侧组合根: banner -> 目录信任 -> 装配 -> `Repl.run()` | 为什么 banner 要先于信任解析渲染? |
| 3 | `interfaces/runtime/tool_wiring.py::build_tool_stack` (493 行) | **CLI 与 Web 共用的装配点**. 从受保护路径到协调器的完整顺序 | 装配顺序里哪几步是安全约束, 换顺序会怎样? |
| 4 | `application/intent_router.py` (118 行) | 一行输入被识别成什么意图 | `#` 开头为什么只在前台 TTY 下才算人工 Shell? |
| 5 | `domain/intents.py` (152 行) | `SessionMode` 四档, `InputOrigin`, `UserIntent` 家族 | `_MODE_LADDER` 为什么不复用枚举声明顺序? |
| 6 | `interfaces/cli/repl.py::Repl._dispatch` (314 行) | 意图分派 | Ctrl-C 为什么不抛异常而是设 CancelToken? |
| 7 | `application/agent_turn/agent_turn_service.py` (755 行) | `handle_user_message` -> `_run_loop` -> `_outcome_from_stop` | 为什么说写文件与起子进程只经它一处发生? |
| 8 | `application/agent_loop/builtin_loop.py` (1220 行) | ReAct 主体, 分两次读 | 模型一次要三个工具时会发生什么? |
| 9 | `application/tool_request/dispatcher.py` (92 行) | 循环与安全管线之间的唯一通道 | `PolicyContext` 为什么每次调用重新构造, 而不是一轮开始时算一次? |
| 10 | `domain/agent/actions.py` (150 行) | `LoopAction` / `LoopObservation` / `ObservationDisposition` | `is_error` 与 `disposition` 为什么是两个字段? |
| 11 | `domain/agent/stop.py` (78 行) | `LoopStopReason` 与 `StopClassification` | 文件末尾那个 `assert` 挡住了什么? |
| 12 | `domain/agent/run_events.py` (421 行) | 28 种运行事件与它们的 payload | 为什么每种事件一个 frozen 类而不是自由 dict? |
| 13 | `application/agent_run/events.py` (110 行) | 进程内事件总线 | 订阅者抛异常时为什么不能让它冒泡? |
| 14 | `interfaces/cli/run_renderer.py` (479 行) | 终端唯一写入者 | 为什么正文要按 `request_id` 分块? |

**配套 ADR**: 0010 (循环架构), 0016 (运行事件流), 0025 (Web 主入口), 0028 (结构收敛).

**这一段的核心不变量**: `BuiltinAgentLoop` 只产出 `LoopAction` / `LoopStop` 这些值对象.
它不写文件, 不删文件, 不起子进程 —— 拿不到 `ToolRegistry`, 也拿不到 `ToolRuntime`,
唯一的对外通道是 `CoordinatorToolDispatcher`. 读第 8 站时刻意验证这一点.

> ADR-0028 之后 `AgentLoop` 与 `ToolDispatcher` 两个 ABC 已删除: 各自只有一个实现,
> 且实现与抽象同层同文件, 不承担依赖倒置. "唯一通道"这条约束改由 `check_arch.py` 的
> `SIBLING_BANS` 守着. 旧材料里提到 `application/agent_loop/loop.py` 的, 那个文件已经
> 不在了.

## 3. 主线二: 一次工具调用是怎么被裁决的

**这是整个项目最核心的部分**, 也是最难的. 预留最多时间.

先读值对象, 再读管线, 最后读工具实现.

### 3.1 值对象层 (`domain/tool/`)

按这个顺序:

1. `capability.py` (96 行) —— 17 个能力的闭集. 整套解耦的基础.
2. `spec.py` (137 行) —— `ToolSpec`: 能力**上界**, 不是放行证明. 注意它**没有**
   `requires_authorization` 也**没有** `risk_level`, docstring 解释了为什么. 顺带看
   `TargetDeclarationAbility` 三档 (STATIC / EXPANDABLE / OPAQUE) 与它的 `permits`.
3. `plan.py` (250 行) —— `ToolPlan`: 工具与安全之间**唯一**的事实载体. 这是全项目最
   重要的一个值对象, 值得读两遍. 注意 `TargetResolution` 与 `DeclarationConfidence`
   的区别, 以及 `FileStateBinding` (ADR-0027 的执行前复核锚点).
4. `authorization.py` (150 行) —— `ExecutionAuthorization`: 不透明, 单次使用. 看
   `validate_narrowing` —— 安全模块只能**收缩**计划, 不能放宽.
5. `hashing.py` (75 行) —— 为什么不能用 `dataclasses.asdict`, 以及 `compare=False`
   的字段为什么一律不进哈希. 这份 docstring 是理解 §9 那条"缓存"误解的钥匙.
6. `errors.py` / `result.py` / `catalog.py` / `tool_call.py` —— 快速过一遍.

**读完能回答**: 为什么"新增工具不需要修改安全模块"? 这句话靠什么机制成立?

### 3.2 管线 (`application/tool_request/` + `application/security/`)

`application/tool_request/coordinator.py` 是整条管线的主干 (706 行). **先只读
`handle` 与 `_resolve` 两个方法**, 把它们调用的每个私有方法当黑盒, 建立顺序感:

```text
handle: bind_turn -> 补 workspace_id -> 绑 invocation_id 到日志上下文
_resolve:
  catalog_for(mode) -> _check_availability -> _prepare
    -> ToolAuthorizationService.evaluate
    -> DENY / ASK / ALLOW 三条分支
    -> (ASK 时) 阻塞审批 -> _revalidate 重新 prepare + 重新裁决 + 比对 ApprovalBinding
    -> _execute: 恢复事务 -> authorization.issue -> 写前审计 -> ToolRuntime.execute
```

然后逐个展开:

| 文件 | 行数 | 职责 |
|---|---|---|
| `tool_request/catalog_predicates.py` | 42 | mode 能力门. 只有 plan 档收窄目录, 其余三档差别在裁决 |
| `tool_request/approval_flow.py` | 92 | 审批视图与 `ApprovalBinding` 怎么拼 (从协调器分出去的"拼什么") |
| `tool_request/recovery_flow.py` | 101 | 恢复事务的登记与收尾 (分出去的"记什么") |
| `tool_request/observations.py` | 247 | 回给模型的 `ToolObservation` 与它的 `reason_code` |
| `tool_request/audit.py` / `run_observer.py` | 99 / 81 | 审计与展示两条独立出口, 都是 ABC |
| `security/authorization_service.py` | 160 | 按能力分派分析器, 校验收缩, 交给策略引擎 |
| `security/analyzers/registry.py` | 111 | 分析器注册表. 按 `Capability` 分派, **不认识工具名** |
| `security/policy_engine.py` | 192 | 最终裁决. `Hard Deny > Mandatory Ask > 围栏边界 > 分析器 Ask > Allow`, 兜底 DENY |
| `domain/security/budget.py` | 109 | 哪些能力可以不问人. **顶替了原先的 `modes.py` 能力预算表** |
| `domain/security/hard_deny.py` | 257 | 结构化判定 + 原始串预扫描两层, 分工而不叠加 |
| `domain/security/protected_paths.py` | 136 | Hard Deny 的路径判据. 只看 realpath |
| `security/learned_rules.py` | 194 | `always` 规则. 注意它落盘, 且按项目分区 |
| `tools/runtime.py::ToolRuntime.execute` | 201 | 唯一执行入口. 授权为空即拒, 且执行前复核 `FileStateBinding` |

`domain/security/budget.py` 值得单独看一眼 —— 它是 ADR-0030 改动的落点:

```text
旧 (modes.py):  这次调用请求了哪些 Capability, 该模式的表里有没有
新 (budget.py): 这次调用要触达的东西, 围栏兜不兜得住
```

两者形状相似 (都是"表外一律 ASK"), 差别在判据的来源: 前者要靠分析器从命令串里推导出
一个准确的能力集合才成立, 后者不需要 —— 围栏在系统调用那一刻说了算.

**配套 ADR**: 0004 (工具系统), 0013 (分层 Shell 安全), **0021 (失败向安全的默认值)**,
0027 (单一安全事实链与执行前复核), **0030 (运行期围栏)**, 0028 (结构收敛).
0021 记录了一轮审计发现的系统性缺陷, 读它能理解这套设计在防什么.

**读完能回答**:
- 人类批准之后为什么还要重新 prepare 一遍?
- `ApprovalBinding` 只有 6 个字段, 为什么够? (提示: 看它的 docstring 说 tool_name,
  spec_hash, target_set_hash 去哪了)
- `plan_hash` 里为什么不含 `filesystem_view_version`?
- plan 档下模型硬要调 `shell_run` 会发生什么? 有几道防线?

### 3.3 工具实现 (`application/tools/`)

先读 `tool.py` (72 行, `Tool` ABC) 和 `builtin/base.py` (285 行, 共享助手), 然后按
**从简到繁**:

```text
planning_tools.py  最简单. 五个工具共用一个 prepare, PLAN_ONLY, 空 PlanEffects
memory_tools.py    同上. 顺带看 domain/memory/secrets.py 怎么挡住疑似凭证
artifact_read.py   入参是内容哈希不是路径 —— 目标集合"机制上封闭"的最小例子
fs_read.py         看它怎么按调用现场决定是 WORKSPACE_READ 还是 EXTERNAL_READ
fs_find.py         看 EXPANDABLE: 在 prepare 里把 glob 展开成封闭集合; 输出形态跟着
                   pattern 走 (tree_view.py 是它不带 pattern 时的渲染)
search_text.py     看它为什么不 shell out 到 grep/rg; 正则由 `regex` 的 timeout 兜底,
                   不再按语法拒绝
code_definitions.py 看它与 search_text 的分工: 语法树只出定义, 不出 import 与调用点
git_read.py        看它为什么声明 SPAWN_PROCESS 却不声明 EXECUTE_SHELL
fs_apply_patch.py  最长的写入口 (557 行). 一个信封 = 一次审批 = 一个恢复点.
                   配套 patch_envelope.py (信封语法), patch_apply.py (施加),
                   text_edit.py (FIND 段的容差对齐)
shell_run.py       能力上界最宽的一个. OPAQUE + 12 个能力
```

> ADR-0029 按"动作"重组过这批模块: `fs_scan_tree` + `fs_list_files` 合并成 `fs_find`;
> `fs_create_file` / `fs_edit_file` / `fs_delete` / `fs_create_directory` / `fs_move`
> 五个入口合并成 `fs_apply_patch`. ADR-0036 又把工具名从点号分段 (`fs.read`) 改成
> 下划线分段 (`fs_read`). 旧材料里的工具名基本都要换算一遍.

当前注册的 15 个工具 (见 `interfaces/runtime/tool_wiring.py` 的 `register_all`):
`plan_read`, `plan_write`, `todo_read`, `todo_write`, `todo_set_status`,
`artifact_read`, `memory_write`, `memory_forget`, `fs_find`, `fs_read`, `search_text`,
`code_definitions`, `git_read`, `fs_apply_patch`, `shell_run`.

**读完能回答**: 同样是读文件, 为什么 `fs_read` 在 plan 档可见而 `shell_run` 不可见?
`git_read` 会起子进程, 它为什么也能进 plan 档目录?

## 4. 主线三: 一条 Shell 命令是怎么被处理的

这条线可以晚一点走, 但不能不走 —— 它是 `shell_run` 能存在的全部理由.

ADR-0030 之后这条线分成两半, 而且**承重的是后一半**: 解析与分析回答"这条命令看起来会
做什么", 围栏回答"它实际能碰到什么". 前者用于展示, 审批与学习规则; 后者用于兜底.

### 4.1 解析 (`domain/security/shell/`)

| # | 文件 | 行数 | 看什么 |
|---|---|---|---|
| 1 | `tokens.py` | 366 | 分词 |
| 2 | `parser.py` | 217 | 按方言分派 |
| 3 | `posix.py` / `cmd.py` / `powershell.py` | 236 / 166 / 218 | 三种方言的语法模型 |
| 4 | `command_plan.py` | 301 | 解析结果: `CommandPlan` |
| 5 | `commands.py` | 549 | **一条命令一条记录**: 影响形态 + 参数结构 + 能否证明只读. ADR-0028 把原先散在三处的命令知识合到这里 |
| 6 | `effects.py` | 72 | 查 `commands.py` 的表, 再处理两类查不出来的情形. **表外默认值是"可能写"** |
| 7 | `arguments.py` | 128 | 一个单元的 argv 里哪几个是路径候选. 错了会**凭空造出目标** |
| 8 | `expansion.py` | 152 | 受控目标展开: STATIC / FORGE_EXPANDED / DYNAMIC 的判据 |
| 9 | `builtins.py` | 174 | 各方言的内建命令. 不认识它们, `export FOO=1` 会被判成"找不到可执行文件" |
| 10 | `wrappers.py` | 661 | 全项目最烧脑的一份. 剥 `sudo` / `env` / `xargs` / `bash -c` 直到真实命令 |

### 4.2 分析器 (`application/security/analyzers/`)

| 文件 | 行数 | 职责 |
|---|---|---|
| `shell_analyzer.py` | 315 | 把解析结果变成能力集合 |
| `shell_effects.py` | 270 | 把解析结果变成 `PlanEffects` (碰哪些路径, 怎么碰) |
| `executable_binding.py` | 184 | 这条命令实际跑哪个文件 + 它裁决后有没有被换掉 |
| `script_binding.py` | 151 | 读脚本正文. **它不分析脚本做了什么** —— 只记住读到的是哪一份 |
| `workspace_analyzer.py` | 109 | 不经 Shell 的工具 (如 `fs_apply_patch`) 的路径检查 |
| `network_analyzer.py` | 44 | 网络能力 |
| `unknown_analyzer.py` | 46 | 无法自证的能力走最保守路径 |

`script_binding.py` 的前身 `ScriptExecutionAnalyzer` 有 384 行, 其中绝大部分是风险模式
匹配, LLM 分类器调用与从正文推导能力. ADR-0030 全删了 —— 脚本跑在围栏里, 越界的访问由
内核拒绝, 不必读它的正文找危险. 剩下的三件事围栏替代不了: 审批界面逐字展示, 学习规则绑
内容哈希, 审计记录被拦的是哪段代码.

### 4.3 围栏 (`domain/execution/` + `infrastructure/execution/sandbox/`)

| 文件 | 行数 | 看什么 |
|---|---|---|
| `domain/execution/fence.py` | 82 | `FencePolicy`: 写 allowlist, 读 denylist, 网络开关. **这里没有任何命令知识** |
| `domain/execution/profile.py` | — | `IsolationLevel` 只有两档, 由启动时的**行为自测**填, 不由平台名填 |
| `infrastructure/execution/sandbox/selection.py` | — | 按平台唯一确定候选, 自测不过就如实降级为 UNCONFINED, **不换一个能跑起来的接着试** |
| `infrastructure/execution/sandbox/` | — | `seatbelt.py` (macOS), `bubblewrap.py` (Linux), `wsl2.py` (Windows), `none.py` |

`fence.py` 的 docstring 记了一件实测出来的事: 读与写两条边界的形态不对称. 写可以用
allowlist, 读**只能**用 denylist —— Seatbelt 下 `(deny default)` 会让子进程连 `/bin/sh`
都起不来. 这意味着受保护路径仍然要逐条列举, 漏一条就是凭证暴露, 而这条风险只在 Linux
上由 bubblewrap 的 mount namespace 解决.

**配套 ADR**: 0013 §3 / §6 / §7 (解析), **0030 (围栏, 决策 1-7)**, 0027 (执行前复核),
0028 规则 D (命令知识收拢).

**读完能回答**:
- `bash -lc 'rm -rf /'` 为什么不会被判成"只是跑了个 bash"?
- 围栏自测不过时会怎样? 为什么不能退回"用静态分析补偿"?
- 一台装了 bwrap 但 userns 被关掉的机器上, `select_provider` 返回什么? 调用方该看哪个
  字段?
- `execution_profile_hash` 里为什么必须含围栏自测结论?

## 5. 支线: 按需深入

主线走完之后, 这几块可以独立读, 互不依赖:

| 支线 | 入口 | 配套 ADR | 什么时候读 |
|---|---|---|---|
| 上下文压缩 | `application/context/manager.py` (276 行) | 0032, 0037 | 想理解 `/compact` 与"历史怎么塞进窗口" |
| 跨会话记忆 | `application/memory/memory_service.py` (182 行) | 0033 | 想理解模型为什么记得上一轮的事 |
| 计划与待办 | `application/planning/planning_service.py` (370 行) | 0022 | 想理解 `plan_write` 与 `/plan-doc` |
| 计划评审 | `application/planning/plan_review.py` (143 行) | 0023, 0038 | 想理解 plan 档"同意并执行"为什么能升到 auto |
| 工作区恢复 | `application/recovery/coordinator.py` (519 行) | 0015 | 想理解 `/undo` 与"首次破坏性写入屏障" |
| 系统提示词 | `application/prompt/system_prompt_builder.py` (360 行) + `templates/` | 0018, 0031, 0039 | 想改模型行为 |
| 本地 Web 控制面 | `interfaces/web/runtime.py` (532 行), `app.py` (1018 行), `web/src/App.tsx` | 0025 | 想理解产品主入口 |
| 可观测性 | `shared/observability/` + `interfaces/runtime/diagnostics.py` | 0035 | 要排查线上行为 |
| 人工 Shell | `application/manual_shell/service.py` (140 行) | 0017 | 想理解 `#` 这条独立信任通道 (仅 `forge cli`) |
| LLM 网关 | `application/llm/gateway/default_gateway.py` (1161 行) | 0011, 0012 | 要接新供应商 |
| 会话存储 | `application/session/session_service.py` (310 行) | 0001, 0008, 0026 | 想理解 `/resume` |
| 终端渲染 | `interfaces/cli/run_renderer.py` (479 行) | 0016 | 要改终端输出 |
| 配置系统 | `application/config/config_service.py` (67 行) | 0005, 0008 | 要加配置项 |

**Web 与 CLI 的关系值得单独说一句**: 它们**不是两套业务**. 两条路径共用
`interfaces/runtime/` 下的三个组合根, 共用同一套 application 与同一条安全管线; 差别只在
适配器 —— 终端渲染换成 SSE + React (`web/src/runModel.ts` 是 `run_renderer.py` 的对应
物), 阻塞式 TTY 审批换成 `interfaces/web/approval.py` 的 broker. 两者不能同时对同一项目
运行, 由项目级 `forge.lock` 保证.

## 6. 这个代码库的阅读技巧

**docstring 承载"为什么", 不是"是什么".** 这是这个项目最重要的阅读入口. 大部分模块与
关键方法的 docstring 记录的是**当初为什么这么选, 以及不这么选会怎样**. 例如
`text_edit.py` 里"容忍不携带信息的差异, 不容忍携带信息的差异"那段, 或者 `hashing.py`
里为什么不能用 `dataclasses.asdict`. **看到长 docstring 不要跳过, 那里面是设计决策.**

**注释里带"曾经" / "早先" / "原来" / "原先"的地方是踩过的坑.** 全项目搜这几个词, 能捞出
一批真实事故的记录. 例如 `plan.py` 里 `filesystem_view_version` 为什么标 `compare=False`,
或者 `approval.py` 里那四个字段为什么从 `ApprovalBinding` 删掉.

**删掉的东西会留下墓碑.** 这个库的习惯是删代码时在原位或替代者的 docstring 里写清
"删的是什么, 为什么它的理由不成立了". `domain/security/budget.py` 开头那段"顶替了原先的
`modes.py`", `check_arch.py` 里那条被删的 SIBLING_BAN 的注释, 都是这种. 顺着墓碑读比读
git log 快.

**不变量集中在四处**: 值对象的 `__post_init__`, 模块级 `assert` (如 `stop.py` 末尾),
`scripts/check_arch.py`, 以及 `scripts/check_abstractions.py`. 想知道"什么是不能违反的",
看这四处.

**`__all__` 是模块的对外边界.** 没进 `__all__` 的名字是内部实现.

**测试是第二份文档.** 测试函数名是完整的英文句子, 读测试名就知道系统承诺了什么.
91 个测试文件按主题分组:

```text
tests/tools/         工具行为 (117 例)
tests/security/      安全裁决与回归 (146 例). 文件名带 regressions 的都是真实事故
tests/tool_request/  管线接缝 (20 例)
tests/agent_run/     循环与事件时间线 (151 例)
tests/prompt/        提示词编译与指纹 (85 例)
tests/planning/      计划, 待办与计划评审 (72 例)
tests/context/       上下文压缩与去重 (26 例)
tests/memory/        记忆边界 (29 例)
tests/observability/ 日志, 读数与链路追踪 (35 例)
tests/web/           Web API 与事件流 (32 例)
tests/manual_shell/  人工 Shell 的信任边界 (65 例)
tests/commands/      斜杠命令 (50 例)
tests/execution/     围栏 (21 例)
tests/llm/           网关流式解析 (5 例)
tests/support/       共享替身与循环夹具
```

## 7. 用改动验证理解

读懂的标准不是"看完了", 是"能预测改动的后果". 建议按顺序做这几个练习:

1. **跑起来**: `make ci`, 然后 `poetry run forge cli`. 看一次真实对话的终端输出.
   再跑一次裸 `poetry run forge`, 对比同一条链路在浏览器里长什么样.
2. **加一个最小工具**: 照 `planning_tools.py` 里 `TodoReadTool` 的形状写一个
   `echo_text`, 注册进 `interfaces/runtime/tool_wiring.py`. 验证: 它自动出现在 `/tools`
   里, 且在四档模式下的可见性符合你的预期.
3. **故意违反分层**: 在 `domain/tool/plan.py` 里 `import rich`, 跑 `make arch`. 看它
   怎么拦你. 再试着在 `application/tools/` 里 import 一次 `application/security/`.
4. **故意破坏不变量**: 把 `fs_apply_patch` 的 `target_declaration_ability` 从
   `EXPANDABLE` 改成 `STATIC`, 跑 `tests/tools/test_fs_apply_patch.py` 与
   `tests/tool_request/test_declaration_bound.py`. 这类缺陷真实发生过.
5. **改内置提示词**: 改 `application/prompt/templates/blocks/` 下任一份模板的一个字,
   跑 `tests/prompt/`. 看指纹用例怎么逼你升 `PROMPT_TEXT_VERSION`. 再试着在某个 Python
   文件里直接写一句中文进 `PromptBlock(body=...)`, 跑 `make arch`.
6. **读一条完整审计**: 跑一次带工具的对话, 然后看
   `~/.forge/projects/<project_id>/sessions/<session_id>/events.jsonl`. 把事件序列和你
   读的代码对上. 同时对着 `~/.forge/logs/forge-latest.log` 看同一次调用的日志侧.
7. **看围栏真的立起来没有**: `/diagnostics` 看本机的 `IsolationLevel`. 如果是
   UNCONFINED, 顺着 `select_provider` 的自测报告找原因 —— 这一步能让 §4.3 从概念变成
   这台机器上的事实.

## 8. 当前已知的缺口

**这些地方不要浪费时间困惑** —— 它们是有意为之或尚未落地, 不是你读漏了:

| 现象 | 状态 |
|---|---|
| 代码里搜不到 `LoopHook` / `LoopState`, 只在 docstring 里被提到 | ADR-0010 预留的扩展点, 从未落地. 事件总线明写"需要改变循环方向的能力必须实现 LoopHook", 而那个类目前不存在 |
| 没有 mid-turn resume | 同上. `LoopStopReason` 里的 `RESUMABLE_PAUSE` 一档目前靠重新起一轮实现 |
| ADR-0034 (分层上下文组装与混合语义检索) 无对应代码 | 状态仍是 Proposed, 未实现. 当前压缩走的是 ADR-0032 的预算驱动路径 |
| 没有 MCP 接入 | 设计文档里有, 代码里没有. `Capability` 与 `ToolSpec` 的 docstring 里为它留了位置 |
| 没有 Sub-Agent | 同上 |
| `SessionMode` 不落盘 | 刻意的: `state.json` 必须能从 `events.jsonl` 重建, 而 mode 没有对应事件. 见 `session_service.py::set_mode` 的 docstring |
| `docs/02-detailed-design.md` §3.5 列了六种模式与一个 `ModePolicy` 类 | 文档漂移. 以四档 (`PLAN`/`ACCEPT_EDITS`/`AUTO`/`FULL_ACCESS`) 为准, `ModePolicy` 已随 ADR-0009 废弃删除 |
| `docs/01-overview-design.md` §5.1 提到 MCP SDK 与 `ModePolicy` | 同上 |

已经补上, 旧材料里仍写着"未实现"的 (看到别再当缺口):

| 曾经的缺口 | 现状 |
|---|---|
| plan 档没有计划评审 | 已实现. `application/planning/plan_review.py`, ADR-0023 + ADR-0038 |
| 没有 `plan_read` / `todo_write` 等工具 | 已实现. `application/tools/builtin/planning_tools.py`, 五个 |
| 沙箱恒为 `NO_SANDBOX` | 已实现. Seatbelt / bubblewrap / WSL2 三个 Provider + 启动自测, ADR-0030 |
| 没有上下文压缩 | 已实现. ADR-0032 + ADR-0037 |
| 没有跨会话记忆 | 已实现. ADR-0033 |
| 没有结构化日志 | 已实现. ADR-0035 |

**冲突时的裁定顺序**: 已接受的 ADR > 最新日期的 roadmap > `02-detailed-design.md`.
见 `docs/05-acceptance-standards.md` §9. 同一主题有多份 ADR 时以编号大的为准, 并注意
§1.3 那张替代关系表.

## 9. 常见误解

**"提示词能约束模型行为"** —— 不能. 提示词只降低无效请求. 真实边界是 Tool Catalog,
PolicyEngine, ApprovalService, ToolRuntime 与围栏. 读 ADR-0018 §9.1.

**"审批通过就是授权"** —— 不是. 批准之后必须重新 prepare, 重新裁决, 逐项比对
`ApprovalBinding` 的 6 个字段 (其中 `plan_hash` 覆盖了工具名, spec_hash, 目标集合与能力
词汇版本), 才能签发 `ExecutionAuthorization`. 读 ADR-0004 §6.1 与
`domain/security/approval.py`.

**"签发了授权就一定会执行"** —— 不一定. `ToolRuntime.execute` 在起进程之前还要复核
`FileStateBinding`: 裁决时读到的可执行文件与脚本正文, 到这一刻有没有被换掉. 读 ADR-0027.

**"有个 LLM 分类器在判断命令危不危险"** —— **没有了**. ADR-0030 删掉了它, 连同风险缓存
与静态命令证明. 现在的判据是围栏边界: 命令能不能跑由内核在系统调用那一刻说. 静态分析
留下来的部分只用于展示, 审批与学习规则绑定, 不承担兜底.

**"围栏立起来了就不用管受保护路径了"** —— 不对. 读边界只能用 denylist (见
`fence.py` 的 docstring), 所以受保护路径仍然要逐条列举, 漏一条就是凭证暴露. 只有 Linux
上的 bubblewrap 靠 mount namespace 绕开了这条.

**"批准一份计划等于批准计划里的命令"** —— 不是. `plan_review.py` 明写它不产生
`ExecutionAuthorization`, 不写 learned rules. "同意并执行"只是把 PLAN 升到 AUTO, 与手敲
`/auto` 完全等价; 升档之后每一步仍然逐次走完整条管线.

**"能力声明可以证明工具安全"** —— 不能. `declared_capabilities` 是**上界**, 只用于目录
过滤和一致性校验. 声明只能缩小信任, 不能证明安全. 读 ADR-0004 §3.

**"记忆是模型说了算的, 那它能影响裁决吗"** —— 不能, 而且这条边界是机器守的.
`check_arch.py` 有一条 `application.memory` 不得 import `application.security` 的
SIBLING_BAN —— 记忆之所以敢做静默写入, 全部承重就在这一条上. 读 ADR-0033 决策 3.

## 10. 一份两周的日程建议

| 天 | 内容 |
|---|---|
| 1 | 第 1 节建立骨架 + 跑起来 (练习 1) |
| 2-3 | 主线一 (对话链路) |
| 4-6 | 主线二 3.1 值对象 + 3.2 管线. **慢一点, 这是核心** |
| 7 | 主线二 3.3 工具实现 + 练习 2 |
| 8-9 | 主线三 4.1 解析 + 4.2 分析器 |
| 10 | 主线三 4.3 围栏 + 练习 7 |
| 11 | 练习 3-6, 把不变量摸一遍 |
| 12-14 | 按当前任务挑支线深入 |

## 关联文档

- `docs/01-overview-design.md` —— 概要设计与分层职责 (注意 §5.1 提到的 MCP 与
  `ModePolicy` 尚未 / 不再存在)
- `docs/02-detailed-design.md` —— 领域模型与事件 schema (注意 §3.5 已漂移)
- `docs/04-engineering-standards.md` —— 命名, 提交, 测试规范
- `docs/05-acceptance-standards.md` —— 验收标准与文档一致性要求
- `docs/adr/README.md` —— 40 份架构决策记录
- `docs/SYNC-TO-MAIN.md` —— 副本到主仓的回流记录
- `AGENTS.md` —— 命名约定与禁用名
- `src/forgecli/application/prompt/templates/README.md` —— 模型可读正文的组织与改动纪律
