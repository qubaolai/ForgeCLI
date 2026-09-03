# 代码阅读路线

给第一次通读这个代码库的人. 目标不是"读完所有文件", 而是**能独立回答"这次工具调用为什么
被拦下来了"**这类问题.

## 0. 这份文档怎么用

代码规模 (2026-08-31):

| 层 | 文件 | 行数 | 说明 |
|---|---|---|---|
| `shared` | 10 | 1016 | 取消令牌, JSON Schema 校验, 结构化日志与进程内读数 (ADR-0035) |
| `domain` | 87 | 9927 | 纯值对象, 不读文件不起进程 |
| `application` | 133 | 20374 | 用例编排与 ABC 端口 |
| `infrastructure` | 49 | 5013 | 适配器 |
| `interfaces` | 15 | 3093 | 启动入口, 本机 Web, 共享组合根 |
| `tests` | 94 | 18101 | 1169 个用例 |
| `web/src` | 9 | 3142 | React 控制面前端 (ADR-0025) |

> **2026-08-29 的 ADR-0025 决策 1 修订二删掉了整棵终端入口.** `interfaces/` 从 60 个
> 文件 8753 行降到 15 个文件 3093 行 —— 没了 `interfaces/cli/` 全树, 人工 Shell 三层,
> 斜杠命令注册表与 `IntentRouter`, 一共 48 个模块 6627 行. **`forge cli` 不再存在**,
> `tests/test_entrypoint.py` 专门钉了一条用例守着它不复活. 任何提到 REPL, `/config`
> 这类斜杠命令或 `#` 人工 Shell 的旧材料, 都是那次修订之前写的.

**不要按目录顺序读.** 四万行按字母序读完也建立不起图景. 正确的方式是**跟着一次真实请求
走完整条链路**, 中途遇到不懂的值对象再回头翻.

路线分三段:

1. **建立骨架** (约半天): 四份材料, 读完知道"东西大致在哪".
2. **走通三条主线** (约三天): 跟着请求走完三条链路. 这是主体.
3. **按需深入支线**: 用到哪块看哪块.

每一站都给出: 读什么文件 / 配套哪份 ADR / **读完应该能回答什么问题**. 最后一项是自检 ——
答不上来说明这一站没读透, 往下走会越来越吃力.

### 0.1 让运行日志替你走一遍

"跟着一次真实请求走"可以不只是比喻. 从源码起一次服务:

```bash
poetry install && make web-install && make web-build && poetry run forge
```

`make web-build` 不能省: 前端产物自 2026-08-30 起不入库 (`interfaces/web/static/` 在
`.gitignore` 里), 目录不存在时 `create_app` 会直接抛一句说得清的话.

裸 `forge` 在 `127.0.0.1:8765` 起服务并打印一次性启动链接. 打开页面后进 **设置 -> 常规**,
把"日志级别"改成 `debug`, 重启 forge (handler 在进程启动时就装好了, 改完不重启不生效),
然后跑一句话, 读那份日志 (ADR-0035):

```bash
tail -f ~/.forge/logs/forge-latest.log
```

一次带工具调用的对话会按发生顺序留下 `web.request` -> `web.turn.started` ->
`turn.received` -> `prompt.compiled` -> `context.fit` -> `model.request` ->
`model.response` -> `tool.requested` -> `pipeline.start` -> `pipeline.prepared` ->
`pipeline.decision` -> `tool.execute.ok` -> `tool.observed` -> `loop.stop` ->
`web.turn.finished`. 每一行的模块路径就是文件路径 (去掉 `forgecli.` 前缀), 照着 open
即可. 带 `.start` / `.ok` / `.error` 三态与 `elapsed_ms` 的行是 `_log.span(...)` 打的,
见 `shared/observability/log.py`.

比通读快的地方在于**顺序是真的**: 文档里的链路图是作者整理过的, 日志里的是这台机器上
刚刚实际发生的, 包括那些文档没写的分支 (重试, 降级, 被闸拦下的调用).

日志的五个开关都在 **设置 -> 常规**里, 各是一条 `logging.*` 配置项: "日志同时写终端"把
日志也打到 stderr, "日志包含 HTTP 库"把 httpx / uvicorn 的记录收进同一个文件.

诊断读数走 `GET /api/v1/diagnostics` —— 日志路径, 网关按 provider/model 的调用与延迟
分桶, 以及各阶段的计数与耗时分位数 (后者要先打开"运行指标采集"). 它**目前没有前端页面**,
拿会话 cookie 直接 curl:

```bash
curl -s --cookie "forge_web_session=$(python3 -c 'import json,pathlib;print(json.loads(pathlib.Path.home().joinpath(".forge/web-secrets.json").read_text())["session"])')" http://127.0.0.1:8765/api/v1/diagnostics
```

## 1. 建立骨架

按顺序读这四份, 不要跳:

### 1.1 `scripts/check_arch.py` (282 行) 与它的三个同伴

**第一个读它, 而不是任何架构文档.** 它是这个项目唯一一份**可执行的**架构说明: 四组规则
写成断言, 违反就在 `make ci` 停下.

四组规则分别回答:

- `LAYER_BANS`: 谁不许 import 谁 (domain 不 import 任何内部层; application 不 import
  infrastructure 与 interfaces; infrastructure 不 import interfaces).
- `BANNED_THIRD_PARTY`: domain 与 application 不许碰 rich, prompt_toolkit, httpx,
  requests, openai, anthropic —— 这条决定了"业务逻辑长什么样". (`prompt_toolkit` 已随
  终端入口从运行时依赖里删掉, 这条禁令留着挡它回来.)
- `SIBLING_BANS`: **8 条**同层互斥. 工具层与安全层互相不可见; `agent_loop` 与 `context`
  只认识 `LlmGateway` 这个 ABC, 不认识 `DefaultLlmGateway`; `memory` 不认识 `security`;
  `agent_loop` 不认识 `memory`; `tool_request` 不认识事件总线与会话写入口.
- **无 import 环**: 模块级 import 图必须是有向无环的. 这条是 2026-08-24 补的 ——
  `application/llm/config` 与 `application/llm/gateway` 的包门面各自藏过一个环, 都是
  靠 import 顺序活着的.

> 原先有 14 条 SIBLING_BAN. 其中六条守的是人工 Shell 这条独立信任通道 ("人工 Shell 顺便
> 记一条 learned allow rule"看着贴心, 实际是让用户手敲的命令替 Agent 拿到授权), 随人工
> Shell 一起删除. 还有一条 `security -> llm.gateway.default_gateway` 在 ADR-0030 删掉
> LLM 分类器时删掉 —— 注释留在原位说明为什么它的理由不成立了.

`make arch` 还跑另外三个脚本, 一起读:

- `scripts/check_abstractions.py` (119 行, ADR-0028): 一个 ABC / Protocol 只在满足
  A1 依赖倒置, A2 多实现, A3 开放扩展点三条之一时才保留. 当前 32 个抽象过关. `AgentLoop`
  与 `ToolDispatcher` 就是按这条判据删掉的 —— 只有一个实现, 且实现与抽象同层. A3 只剩
  三个: `Tool`, `CapabilityAnalyzer`, `ModelSelection`.
- `scripts/check_prompt_text.py` (117 行, ADR-0031 / ADR-0039): 送进模型上下文的参数里
  不得出现含中文的字符串字面量. 正文一律从 `application/prompt/templates/` 渲染.
- `scripts/check_deps.py` (151 行, ADR-0040): 依赖声明与真实 import 必须对得上, 两个
  方向都守, 当前 17 个直接依赖全部有 src 消费者. **声明少了**会跑得好好的 —— 传递依赖
  把包装进了环境, 直到上游哪天换掉它; `interfaces/web/app.py` 直接 import 的 `starlette`
  与 `pydantic` 就这么靠 fastapi 蹭了很久. **声明多了**同样安静: `pathspec` 在依赖表里
  躺着但全库零 import, 照样要走安装, 锁定, 许可证审查与 CVE 跟踪. 它也是删终端入口时
  抓出 `prompt-toolkit` 已无消费者的那一步.

**读完能回答**: 为什么 `application/tools` 里没有任何 `import ...security`?
为什么 `AgentLoop` 这个 ABC 被删了, 而 `Tool` 这个 ABC 留着?

### 1.2 `AGENTS.md` + `docs/01-overview-design.md` §5.1

命名约定, 分层职责, 禁用名 (`AgentWorkflow` / `BuiltinWorkflow` / `WorkflowResult`, 见
已废弃的 ADR-0003).

### 1.3 `docs/adr/README.md`

41 份 ADR (0000-0040) 的索引与状态. **不要现在全读**, 只记住有这么一批东西, 以及哪几份
已经不作数:

| ADR | 状态 |
|---|---|
| 0003 | Discarded, 由 ADR-0010 取代 |
| 0009 | Discarded, `ModePolicy` 那套口径不再成立; `SessionMode` 已拆成隔离与审批两个正交轴, 四档只是预设 |
| 0007 | Superseded by ADR-0025 (交互式会话入口与斜杠命令整体删除) |
| 0017 | Superseded by ADR-0025 (人工 Shell 整体删除, 无替代品) |
| 0019 | Superseded by ADR-0030 |
| 0020 | Partially Superseded by ADR-0030 (LLM 安全分类器整体删除) |
| 0024 | Superseded by ADR-0030 |
| 0014 | 保留沙箱生命周期与自测条款, Provider 选型先后由 0019, 0030 取代 |

**ADR-0030 是一次大改**: 它删掉了 LLM 安全分类器, 风险缓存与静态命令证明, 改由运行期
围栏 (Seatbelt / bubblewrap / WSL2) 与工作区快照兜底. 读到任何提"分类器"的旧材料时,
先确认它是不是在 0030 之前写的.

**ADR-0025 有两次修订, 第二次是删代码**: 2026-08-21 的第一次修订用 `forge cli` 保下了
整棵终端入口, 理由是开不了浏览器的远程会话与 `#` 人工 Shell 这条信任通道. 2026-08-29 的
修订二推翻了它 —— 那两个理由本身没错, 错的是没算清它保下了多少东西: 决策 2 与决策 33
要求斜杠命令能改的每一项配置 Web 都要有入口, 于是每一个配置面都实现了两遍, 两份实现共用
application 服务, 但表单, 文案与校验时机各写各的, 改一处漏一处不会报错. 代价写在 ADR 里:
SSH 场景要自己转发 8765 端口, `#` 人工 Shell 没有替代品 (受控的 `shell_run` 不受影响).

**ADR-0040 是一份治理决策, 落地是分批的**: 它定了两件事 —— 通用机制优先交给成熟依赖
(filelock, sse-starlette, Pydantic, libgit2, prompt-toolkit 已落地; 其中 prompt-toolkit
随终端入口一起走了), 以及开放世界里的命令名 / 路径名 / 环境变量名这类穷举表**不得承担
自动放行责任**. 它同时修订了 ADR-0011 的封闭 provider 注册表, ADR-0012 的 tokenizer /
cache / 熔断实现方式, 以及 ADR-0035 的日志开关归属.

**它的 §4 有四条是"没做", 而且四条的"没做"各不相同 —— 这一段比落地的部分更值得读**,
因为它是这个库少见的把实测数据连同结论一起留在原位的地方:

| 条目 | 结论 | 一句话理由 |
|---|---|---|
| 4.1 官方 OpenAI SDK | **不采用** | 评审时撤回, 传输栈保持现状 |
| 4.6 platformdirs | **不采用** | 评审时撤回 |
| 4.8 Tree-sitter 替代手写 Shell 解析 | **改期**, 另立 ADR | 语法可用, 但**换掉它的理由不成立** |
| 4.9 detect-secrets 替代自有凭证检测 | **不采用** | 装了 1.5.0 实测两种配置, 都不如现状 |

4.8 值得单独看: 作者用九条对抗性输入实测了手写解析器的不完整之处, 结论是**每一处都失败
向安全** —— `$'\x72\x6d' -rf /tmp/x` 解出的可执行文件在受控 PATH 上找不到, 直接 DENY;
三条 `parse_error` 走 UNPROVEN 落到 ASK. 没有一处是绕过. 所以真实收益是**可用性**
(三次本不必要的打断) 而不是安全性, 而这与本 ADR 给的论证和验收标准都不是一回事. 改期
条件写了三条, 其中一条是原型暴露的两个没有文档的隐性约定: 重定向的 fd 会落进 `argv`,
命令替换用 `\x00subN\x00` 占位.

> 注意别被 grep 误导: `tree-sitter-language-pack` **在依赖表里, 也确实在用** —— 但只用于
> `application/tools/builtin/find_definition.py` 的代码定义提取. Shell 解析仍然是
> `domain/security/shell/` 那一整套手写实现.

4.9 的第一行表格是决定性的: 开熵检测器能抓到全部十种真凭证, 但**文件路径, 40 位 commit
sha, 构建产物名, 驼峰标识符全部误报**. 记忆是静默写入 (ADR-0033 决策 2), 误报的表现是
"它记不住东西"且没有人看得到原因 —— 一个记不住文件路径的记忆系统, 用户会在第三次的时候
关掉它. 只开厂商 + 关键词则漏掉四种 (OpenAI, Anthropic, Google API key, npm token), 前缀
表全认得. 那次实测仍有真实产出: 它暴露了现有实现的两个真误报, 已修 (`_TOKEN` 的字符集
去掉 `/`, `_is_random_token` 增加"没有八个以上同类字符连排" —— 两处的理由都写在
`domain/memory/secrets.py` 的注释里). 但 ADR 里说的配套用例文件
`tests/memory/test_secret_shapes.py` **不存在, 也从未存在过**, 见 §8.

### 1.4 `src/forgecli` 的目录树

```text
shared/          不属于任何一层的基础件: 取消令牌, JSON Schema, 日志与读数
domain/          纯值对象. 认识这里的名字就认识了整个系统的词汇
  agent/         循环动作, 循环输入, 停止原因, 23 种运行事件
  tool/          ToolSpec / ToolPlan / Capability / ExecutionAuthorization
  security/      安全词汇, Hard Deny, 受保护路径, 能力预算 + Shell 解析 (shell/ 子包)
  execution/     执行画像, 环境净化, FencePolicy (围栏边界)
  context/       上下文预算与压缩结果 (ADR-0032)
  memory/        记忆条目与疑似凭证识别 (ADR-0033)
  planning/      Plan 与 TodoList (ADR-0022)
  prompt/        PromptBlock / PromptSnapshot
  recovery/      恢复检查点与变更集
  conversation/  ChatMessage 与 turn
  session/       会话事件 (15 种 EventType) 与快照
  workspace/     工作区边界与路径归一
  model/         LLM 请求 / 响应 / 流式
  config/        配置键与合并结果
  intents.py     SandboxLevel / ApprovalPolicy 两个正交轴, SessionMode 是它们的组合, InputOrigin 两档
application/     用例编排. ABC 端口也在这里
  agent_loop/    ReAct 循环 (BuiltinAgentLoop)
  agent_turn/    唯一写文件, 起子进程的驱动方 + TurnCancelSource
  agent_run/     运行事件总线与观察者 (ADR-0016)
  tool_request/  工具请求协调器 (安全管线的编排者) + 审批流 + 恢复流 + 调度器
  tools/         工具机制层 + builtin/ 十五个内置工具
  security/      安全策略层 (分析器, 策略引擎, 审批, 学习规则)
  context/       上下文压缩与回合内去重 (ADR-0032)
  memory/        跨会话记忆 (ADR-0033)
  planning/      计划 / 待办服务与计划评审 (ADR-0022, ADR-0023, ADR-0038)
  recovery/      恢复事务
  prompt/        系统提示词编译 + templates/ 模型可读正文
  llm/           LLM 网关与模型目录
  workspace/     ExecutionContext 与文件系统视图
  project/       项目身份与工作区信任
  session/       会话服务与 resume
  config/        配置服务
infrastructure/  适配器: 文件系统, 子进程, 沙箱 Provider, JSON, HTTP, LLM adapters
                 第三方库的对象只到这一层为止 (ADR-0040 决策 3): libgit2 的
                 Repository 出不了 workspace/pygit2_git_queries.py, 上面拿到的是
                 application/tools/git_queries.py 里那几个纯数据类
interfaces/
  app.py         极薄 Typer 入口 (83 行). 没有子命令, 只解析 --version/--open/--port
  exit_codes.py  进程退出码的封闭定义. 住在这里而不是某个入口的包下
  web/           FastAPI 应用, SSE 事件流, 审批 broker, 按项目装配的 Runtime
  runtime/       组合根: tool_wiring / llm_wiring / logging_wiring / diagnostics
```

`interfaces/` 现在只有两件事: 起进程 (`app.py` -> `web/server.py`) 和装配
(`runtime/`). `application` 与它下面的所有层, 删掉终端入口时一行没动.

**读完能回答**: `ToolPlan` 应该在哪一层? 为什么?

## 2. 主线一: 一次对话是怎么跑完的

从浏览器里敲下一行字, 到页面上出现回复. 这条线最短, 先走它建立"请求在系统里怎么流动"
的感觉.

只有这一条路径了 —— 裸 `forge` 起本机 Web 服务, 业务交互全在浏览器 (ADR-0025 决策 1).

| # | 文件 | 看什么 | 读完能回答 |
|---|---|---|---|
| 1 | `interfaces/app.py` (83 行) | Typer 根回调. `invoke_without_command=True`, 无子命令 | 为什么 `--version` 要写成 eager callback, 而不是回调体里的一个 `if`? |
| 2 | `interfaces/web/server.py` (188 行) | 项目锁, 一次性启动令牌, 信号接管, SSE 优雅收尾 | `capture_signals` 为什么是个空的 contextmanager? 服务停止时为什么要先置 `stopping` 再走 uvicorn 的等待流程? |
| 3 | `interfaces/exit_codes.py` (45 行) | 进程退出码的封闭定义 | 为什么它不住在 web 包里? |
| 4 | `interfaces/web/app.py::LocalControlPlaneGuard` (1059 行, **先只读中间件与路由表**) | Host / 会话 cookie / CSRF 三道校验, 50 条路由的形状 | 为什么它必须是纯 ASGI 中间件, 不能用 `BaseHTTPMiddleware`? |
| 5 | `interfaces/web/runtime.py::ProjectRuntime` (531 行) | **按项目装配一次**的组合根; `start_turn` 起后台线程, `_execute_turn` 是那个线程的最外层 | 一个项目同时只能跑一轮, 这条由谁保证? 后台线程里的异常为什么必须先 `_log.exception` 再压成状态串? |
| 6 | `interfaces/runtime/tool_wiring.py::build_tool_stack` (499 行) | 从受保护路径到协调器的完整装配顺序 | 装配顺序里哪几步是安全约束, 换顺序会怎样? |
| 7 | `domain/intents.py` | `SandboxLevel` x `ApprovalPolicy`, `SessionMode` 是两者的组合, `InputOrigin` 两档 | 为什么隔离与审批要拆成两个轴, 而四档预设只是常用组合? `MODE_PRESETS` 为什么显式写出而不是复用声明顺序? `InputOrigin` 的默认值为什么是 `PROGRAM` 而不是 `WEB_USER`? |
| 8 | `application/agent_turn/agent_turn_service.py` (737 行) | `handle_user_message` -> `_run_loop` -> `_outcome_from_stop` | 为什么说写文件与起子进程只经它一处发生? |
| 9 | `application/agent_loop/builtin_loop.py` (1267 行) | ReAct 主体, 分两次读 | 模型一次要三个工具时会发生什么? |
| 10 | `application/tool_request/dispatcher.py` (92 行) | 循环与安全管线之间的唯一通道 | `PolicyContext` 为什么每次调用重新构造, 而不是一轮开始时算一次? |
| 11 | `domain/agent/actions.py` (150 行) | `LoopAction` / `LoopObservation` / `ObservationDisposition` | `is_error` 与 `disposition` 为什么是两个字段? |
| 12 | `domain/agent/stop.py` (78 行) | `LoopStopReason` 与 `StopClassification` | 文件末尾那个 `assert` 挡住了什么? |
| 13 | `domain/agent/run_events.py` (421 行) | **23 种**运行事件与它们的 payload | 为什么每种事件一个 frozen 类而不是自由 dict? |
| 14 | `application/agent_run/events.py` (110 行) | 进程内事件总线 | 订阅者抛异常时为什么不能让它冒泡? |
| 15 | `interfaces/web/events.py` (143 行) | `WebEventHub`: 把进程内事件变成可重连的有限缓冲 (2048 条) | 事件由 Agent 线程产生, 唤醒为什么必须回到 SSE 自己的事件循环? |
| 16 | `interfaces/web/app.py` 的 `GET /api/v1/events` (502-560 行) | 断线重连补发, `resync_required`, `server_stopping` | 首次连接为什么**不**补发历史? 缓冲被挤掉之后页面怎么恢复? |
| 17 | `web/src/runModel.ts` (459 行) + `RunProcess.tsx` (321 行) | 前端把事件流重建成一轮的时间线 | 刷新页面为什么不会丢掉正在跑的那一轮? |

`interfaces/web/serialization.py` (37 行) 值得顺手看一眼: 它只展开值对象, 枚举和基础
容器, 认不出的类型直接抛 —— 而不是调 `__dict__` 或 `model_dump()`. 那份 docstring 解释
了为什么"图省事"的那条路会让嵌在里面的 dataclass 绕过白名单.

**配套 ADR**: 0010 (循环架构), 0016 (运行事件流), **0025 (本机 Web 控制面, 含两次修订)**,
0028 (结构收敛).

**这一段的核心不变量**: `BuiltinAgentLoop` 只产出 `LoopAction` / `LoopStop` 这些值对象.
它不写文件, 不删文件, 不起子进程 —— 拿不到 `ToolRegistry`, 也拿不到 `ToolRuntime`,
唯一的对外通道是 `CoordinatorToolDispatcher`. 读第 9 站时刻意验证这一点.

> ADR-0028 之后 `AgentLoop` 与 `ToolDispatcher` 两个 ABC 已删除: 各自只有一个实现,
> 且实现与抽象同层同文件, 不承担依赖倒置. "唯一通道"这条约束改由 `check_arch.py` 的
> `SIBLING_BANS` 守着. 旧材料里提到 `application/agent_loop/loop.py` 的, 那个文件已经
> 不在了.

### 2.1 取消与审批: 两处阻塞点

这两件事在终端时代靠 Ctrl-C 与阻塞式 TTY 提问实现, 现在都变成了 HTTP 上的一次请求, 但
application 侧的形状**一个字没改** —— 这正是删掉终端入口没有动到业务的原因.

- **取消**: `POST /api/v1/turns/current/cancel` -> `ProjectRuntime.cancel()` ->
  `TurnCancelSource`. application 只依赖 `shared/cancellation.py` 的 `CancelToken`,
  不感知信号也不感知 HTTP. 循环工厂经 `current()` 把当前 token 挂到 `ModelRequest` 上.
- **审批**: `interfaces/web/approval.py` 的 `WebApprovalBroker` (106 行) 实现
  `ApprovalService`, 阻塞的是**工具线程**, 由页面上的 `POST /api/v1/approvals/{id}/resolve`
  解开. 它的 docstring 值得读: **不设等待超时**. 挂钟到点就判成"没批准", 等于让用户去泡
  杯咖啡的功夫决定这次调用的命运, 而模型收到的是"未获授权"随后整轮停摆 —— 用户回来时
  既看不到审批卡片, 也没有补救入口. 正确的终止条件只有三个: 用户决定, 用户停止这一轮,
  服务退出.

## 3. 主线二: 一次工具调用是怎么被裁决的

**这是整个项目最核心的部分**, 也是最难的. 预留最多时间.

先读值对象, 再读管线, 最后读工具实现.

### 3.1 值对象层 (`domain/tool/`)

按这个顺序:

1. `capability.py` (83 行) —— 17 个能力的闭集. 整套解耦的基础.
2. `spec.py` (137 行) —— `ToolSpec`: 能力**上界**, 不是放行证明. 注意它**没有**
   `requires_authorization` 也**没有** `risk_level`, docstring 解释了为什么. 顺带看
   `TargetDeclarationAbility` 三档 (STATIC / EXPANDABLE / OPAQUE) 与它的 `permits`.
3. `plan.py` (243 行) —— `ToolPlan`: 工具与安全之间**唯一**的事实载体. 这是全项目最
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
| `tool_request/recovery_flow.py` | 104 | 恢复事务的登记与收尾 (分出去的"记什么") |
| `tool_request/observations.py` | 247 | 回给模型的 `ToolObservation` 与它的 `reason_code` |
| `tool_request/audit.py` / `run_observer.py` | 103 / 81 | 审计与展示两条独立出口, 都是 ABC |
| `security/authorization_service.py` | 160 | 按能力分派分析器, 校验收缩, 交给策略引擎 |
| `security/analyzers/registry.py` | 111 | 分析器注册表. 按 `Capability` 分派, **不认识工具名** |
| `security/policy_engine.py` | 211 | 最终裁决. `Hard Deny > Mandatory Ask > 围栏边界 > 分析器 Ask > Allow`, 兜底 DENY |
| `domain/security/budget.py` | 138 | 哪些能力可以不问人. **顶替了原先的 `modes.py` 能力预算表** |
| `domain/security/hard_deny.py` | 289 | 结构化判定 + 原始串预扫描两层, 分工而不叠加 |
| `domain/security/protected_paths.py` | 156 | Hard Deny 的路径判据. 只看 realpath |
| `security/learned_rules.py` | 194 | `always` 规则. 注意它落盘, 且按项目分区 |
| `tools/runtime.py::ToolRuntime.execute` | 199 | 唯一执行入口. 授权为空即拒, 且执行前复核 `FileStateBinding` |

`domain/security/budget.py` 值得单独看一眼 —— 它是 ADR-0030 改动的落点:

```text
旧 (modes.py):  这次调用请求了哪些 Capability, 该模式的表里有没有
新 (budget.py): 这次调用要触达的东西, 围栏兜不兜得住
```

两者形状相似 (都是"表外一律 ASK"), 差别在判据的来源: 前者要靠分析器从命令串里推导出
一个准确的能力集合才成立, 后者不需要 —— 围栏在系统调用那一刻说了算.

`policy_engine.py` 里的 `_NEVER_AUTO` 与 `budget._NEVER_AUTO` 同源却各留一份, 原因写在
注释里: 引擎这一支要置 `mandatory`, 那是 budget 表达不了的.

> **2026-08-30 的审计出口收窄**: `ToolAuditSink` 原先有两个"通用出口" (`approval_event`
> 与 `recovery_event`), 都收一个事件名字符串, 共用 `SessionToolAudit` 里一张 `_EVENT_NAMES`
> 表翻成 `EventType`, 查不到就 `return`. 查下来 `approval_event` **零个生产调用点**
> (审批照样有审计: 走 `policy_decision` 写 `POLICY_DECISION`), 表里 7 行只有 2 行可达.
> 而 `if event_type is None: return` 是一个静默丢弃 —— 名字拼错那条审计就凭空消失, 没有
> 任何东西报错, 而审计的全部价值就是事后能回答"这件事发生没发生". 现在 `recovery_event`
> 直接收 `EventType`, 类型检查接管了原来靠字符串对齐的部分.

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

先读 `tool.py` (72 行, `Tool` ABC) 和 `builtin/base.py` (220 行, 共享助手), 然后按
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
find_definition.py 看它与 search_text 的分工: 语法树只出定义, 不出 import 与调用点
git_read.py        看它为什么一个进程都不起. 查询走 libgit2 (application/tools/
                   git_queries.py 是端口, infrastructure/workspace/
                   pygit2_git_queries.py 是实现), 于是 SPAWN_PROCESS 也不用声明
fs_apply_patch.py  最长的写入口 (591 行). 一段补丁 = 一次审批 = 一个恢复点.
                   配套 patch_envelope.py (补丁语法), patch_apply.py (施加),
                   text_edit.py (FIND 段的容差对齐)
shell_run.py       能力上界最宽的一个. OPAQUE + 12 个能力
```

> ADR-0029 按"动作"重组过这批模块: `fs_scan_tree` + `fs_list_files` 合并成 `fs_find`;
> `fs_create_file` / `fs_edit_file` / `fs_delete` / `fs_create_directory` / `fs_move`
> 五个入口合并成 `fs_apply_patch`. ADR-0036 又把工具名从点号分段 (`fs.read`) 改成
> 下划线分段 (`fs_read`). 旧材料里的工具名基本都要换算一遍.

当前注册的 14 个工具 (见 `interfaces/runtime/tool_wiring.py` 的 `register_all`):
`plan_read`, `plan_write`, `todo_write`, `todo_set_status`,
`artifact_read`, `memory_write`, `memory_forget`, `fs_find`, `fs_read`, `search_text`,
`find_definition`, `git_read`, `fs_apply_patch`, `shell_run`.
每个工具在 spec 里声明自己承担哪个动作 (`ToolAction`), 提示词的工具表据此分组; 没有
`todo_read`, 因为待办全文每轮由 `todo_state` 块进提示词.

**读完能回答**: 同样是读文件, 为什么 `fs_read` 在 plan 档可见而 `shell_run` 不可见?
`git_read` 曾经要靠一张 git CLI 参数白名单才敢进 plan 档目录 (挡 `--ext-diff` /
`--textconv` 跑外部程序, `--output=` 写文件, `-c core.pager=` 指定任意命令), 换成
libgit2 之后那张表整个删了 —— 为什么删得掉?

## 4. 主线三: 一条 Shell 命令是怎么被处理的

这条线可以晚一点走, 但不能不走 —— 它是 `shell_run` 能存在的全部理由.

ADR-0030 之后这条线分成两半, 而且**承重的是后一半**: 解析与分析回答"这条命令看起来会
做什么", 围栏回答"它实际能碰到什么". 前者用于展示, 审批与学习规则; 后者用于兜底.

### 4.1 解析 (`domain/security/shell/`)

| # | 文件 | 行数 | 看什么 |
|---|---|---|---|
| 1 | `tokens.py` | 426 | 分词 |
| 2 | `parser.py` | 223 | 按方言分派 |
| 3 | `posix.py` / `cmd.py` / `powershell.py` | 231 / 213 / 218 | 三种方言的语法模型 |
| 4 | `command_plan.py` | 289 | 解析结果: `CommandPlan` |
| 5 | `commands.py` | 600 | **一条命令一条记录**: 影响形态 + 参数结构 + 能否证明只读. ADR-0028 把原先散在三处的命令知识合到这里 |
| 6 | `effects.py` | 72 | 查 `commands.py` 的表, 再处理两类查不出来的情形. **表外默认值是"可能写"** |
| 7 | `arguments.py` | 128 | 一个单元的 argv 里哪几个是路径候选. 错了会**凭空造出目标** |
| 8 | `expansion.py` | 152 | 受控目标展开: STATIC / FORGE_EXPANDED / DYNAMIC 的判据 |
| 9 | `builtins.py` | 185 | 各方言的内建命令. 不认识它们, `export FOO=1` 会被判成"找不到可执行文件" |
| 10 | `wrappers.py` | 730 | 全项目最烧脑的一份. 剥 `sudo` / `env` / `xargs` / `bash -c` 直到真实命令 |

### 4.2 分析器 (`application/security/analyzers/`)

| 文件 | 行数 | 职责 |
|---|---|---|
| `shell_analyzer.py` | 315 | 把解析结果变成能力集合 |
| `shell_effects.py` | 275 | 把解析结果变成 `PlanEffects` (碰哪些路径, 怎么碰) |
| `executable_binding.py` | 185 | 这条命令实际跑哪个文件 + 它裁决后有没有被换掉 |
| `script_binding.py` | 154 | 读脚本正文. **它不分析脚本做了什么** —— 只记住读到的是哪一份 |
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
| `domain/execution/fence.py` | 98 | `FencePolicy`: 写 allowlist, 读 denylist, 网络开关. **这里没有任何命令知识** |
| `domain/execution/profile.py` | 87 | `IsolationLevel` 只有两档, 由启动时的**行为自测**填, 不由平台名填 |
| `domain/execution/environment.py` | 161 | 交给子进程的环境变量净化 |
| `infrastructure/execution/sandbox/selection.py` | 55 | 按平台唯一确定候选, 自测不过就如实降级为 UNCONFINED, **不换一个能跑起来的接着试** |
| `infrastructure/execution/sandbox/` | 152 / 158 / 234 / 36 | `bubblewrap.py` (Linux), `seatbelt.py` (macOS), `wsl2.py` (Windows), `none.py` |

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
| 上下文压缩 | `application/context/manager.py` (285 行) | 0032, 0037 | 想理解"历史怎么塞进窗口" |
| 跨会话记忆 | `application/memory/memory_service.py` (182 行) | 0033 | 想理解模型为什么记得上一轮的事 |
| 计划与待办 | `application/planning/planning_service.py` (370 行) | 0022 | 想理解 `plan_write` 与计划面板 |
| 计划评审 | `application/planning/plan_review.py` (143 行) | 0023, 0038 | 想理解 plan 档"同意并执行"为什么能升到 auto |
| 工作区恢复 | `application/recovery/coordinator.py` (519 行) | 0015 | 想理解撤销与"首次破坏性写入屏障" |
| 系统提示词 | `application/prompt/system_prompt_builder.py` (360 行) + `templates/` | 0018, 0031, 0039 | 想改模型行为 |
| Web 后端 | `interfaces/web/app.py` (1059 行), `runtime.py` (531 行), `server.py` (188 行) | 0025 | 要加接口或改启动行为 |
| Web 前端 | `web/src/App.tsx` (1545 行), `runModel.ts` (459 行), `RunProcess.tsx` (321 行) | 0025 | 要改界面 |
| 可观测性 | `shared/observability/` + `interfaces/runtime/logging_wiring.py` + `diagnostics.py` | 0035 | 要排查线上行为 |
| LLM 网关 | `application/llm/gateway/default_gateway.py` (1161 行) | 0011, 0012 | 要接新供应商 |
| 会话存储 | `application/session/session_service.py` (310 行) | 0001, 0008, 0026 | 想理解 resume |
| 项目身份与锁 | `application/project/project_service.py` (153 行) | 0008, 0025 | 想理解为什么同一项目只能起一个 forge |
| 配置系统 | `domain/config/config_keys.py` (218 行) + `application/config/config_service.py` (67 行) | 0005, 0008 | 要加配置项 |
| 依赖与穷举治理 | `scripts/check_deps.py` + `application/tools/git_queries.py` | 0040 | 想知道什么该交给依赖, 什么表不能承重 |

**配置项是一份封闭登记, 不是散落各处的字符串**: `domain/config/config_keys.py` 的
`SCHEMA` 是唯一权威 —— 键名, 类型, 默认值, 允许取值, 属于应用级还是项目级, 以及**给人
看的中文名与一句说明**, 全在那一条 `ConfigKey` 上. 加配置项 = 加一条, `GET /api/v1/settings`
直接从它派生, 页面不自己写一份文案. 这条纪律现在还多守住了一件事: 终端入口没了之后,
设置页是唯一的配置入口, 而它一行文案都不是手写的.

这条纪律是有代价换来的: 日志的五个开关原先是 `FORGE_LOG_*` 环境变量, 在设置面板里看不见
也改不了, 而 `FORGE_LOG_LEVEL` 还压在 `logging.level` 配置项上 —— "面板显示 info, 实际
按 debug 在写"这种状态没有任何界面能解释. 现在它们各是一条 `logging.*` 配置项.

**只有一条路径了, 但分层没变**: 删掉终端入口时, `application` 及以下**一行没动**. 这不是
巧合 —— CLI 与 Web 一直共用 `interfaces/runtime/` 下的组合根与同一套 application, 差别
只在适配器. 能整棵砍掉一个入口而业务层无感, 正是分层在这次改动上兑现的那一次.

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
`modes.py`", `check_arch.py` 里那条被删的 SIBLING_BAN 的注释, `domain/intents.py` 里
`InputOrigin.TTY_USER` 那段, `interfaces/exit_codes.py` 里"早先它在 cli 包下"那段, 都是
这种. 顺着墓碑读比读 git log 快.

**不变量集中在四处**: 值对象的 `__post_init__`, 模块级 `assert` (如 `stop.py` 末尾),
`scripts/check_arch.py`, 以及 `scripts/check_abstractions.py`. 想知道"什么是不能违反的",
看这四处.

**`__all__` 是模块的对外边界.** 没进 `__all__` 的名字是内部实现.

**测试是第二份文档.** 测试函数名是完整的英文句子, 读测试名就知道系统承诺了什么.
共 1169 例, 按主题分组:

```text
tests/security/      安全裁决与回归 (354 例). 文件名带 regressions 的都是真实事故
tests/tools/         工具行为 (209 例)
tests/llm/           网关流式解析, 模型/供应商配置校验 (112 例)
tests/prompt/        提示词编译与指纹 (103 例)
tests/planning/      计划, 待办与计划评审 (76 例)
tests/agent_run/     循环与事件时间线 (76 例)
tests/observability/ 日志装配, 读数与链路追踪 (47 例)
tests/web/           Web API 与事件流 (42 例)
tests/memory/        记忆边界 (36 例)
tests/context/       上下文压缩与去重 (34 例)
tests/execution/     围栏 (32 例)
tests/tool_request/  管线接缝 (31 例)
tests/project/       项目排他锁 (11 例)
tests/test_entrypoint.py  进程入口 (6 例). 其中一条钉住 `forge cli` 不再存在
tests/support/       共享替身与循环夹具
```

> 数字会漂, 别照抄; 值得记的是**比例**: 安全与工具两块占了接近一半, 而它们正是这个项目
> "改错了会出事"的地方. `tests/cli/`, `tests/commands/`, `tests/manual_shell/` 三组随
> 终端入口一起删除; 本机上可能还剩几个空目录, 那是 `__pycache__` 的残留, 不在库里.

`tests/test_entrypoint.py` 单独值得看一眼: 被删掉的入口最容易以"顺手加回来"的形式复活,
而复活的那一刻不会有任何东西报错 —— 所以那条 `test_there_is_no_terminal_subcommand`
是专门为此写的.

## 7. 用改动验证理解

读懂的标准不是"看完了", 是"能预测改动的后果". 建议按顺序做这几个练习:

1. **跑起来**: `make ci`, 然后 `poetry run forge`, 在浏览器里走一次真实对话. 注意
   `make ci` 里除了 lint / format / mypy / 四个架构守卫与 1191 个 Python 用例, 还有
   `web-type` / `web-test` / `web-build` 三步前端检查.
2. **加一个最小工具**: 照 `planning_tools.py` 里 `PlanReadTool` 的形状写一个
   `echo_text`, 注册进 `interfaces/runtime/tool_wiring.py`. 验证: 它自动出现在
   `GET /api/v1/tools` 里, 且在各隔离档下的可见性符合你的预期.
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
7. **看围栏真的立起来没有**: 自测结论没有任何界面, 直接问那个函数 ——

   ```bash
   poetry run python -c "from forgecli.infrastructure.execution.sandbox.selection import select_provider; p, r = select_provider(); print(p.name, r.confined, r.failures)"
   ```

   `confined` 为 False 时 `failures` 里就是原因. 这一步能让 §4.3 从概念变成这台机器上
   的事实.
8. **试着把终端入口加回来**: 在 `interfaces/app.py` 上挂一个 `@app.command("cli")`,
   跑 `poetry run pytest tests/test_entrypoint.py`. 这条练习最快说明"删掉的东西怎么
   守住不回来".

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
| `ExitCode.NO_TTY` (4) 与 `UNTRUSTED` (5) 没有任何生产方 | 它们只可能来自 `forge cli`, 随终端入口失去产出路径. 保留在表里是为了不让码位漂移, 不是为了将来还有人用 (ADR-0025 修订二原话) |
| `GET /api/v1/diagnostics` 没有前端页面 | 只能 curl (§0.1). ADR-0035 的读数本身完整 |
| 围栏自测结论 (`IsolationLevel`) 不出现在任何接口或日志里 | `select_provider()` 的报告只回填进 `ExecutionProfile` 与 `ToolStack.confined`, 装配时一行都没记. 要看只能自己调那个函数 (练习 7) |
| `EventType.SLASH_COMMAND` 没有产出方却删不掉 | `from_dict` 用 `EventType(...)` 还原, 而终端入口时代的 `events.jsonl` 里真有 `"slash_command"` 行. 落盘取值只增不减 —— 同批的 `APPROVAL_REQUESTED` / `APPROVAL_RESOLVED` / `CLASSIFIER_INVOKED` / `RECOVERY_PERFORMED` 就删得掉, 因为全 git 历史里它们只在一张名字表里出现过, 从未被写进过文件 |
| `domain/memory/secrets.py` 没有直接单元测试 | ADR-0040 §4.9 说"配套用例见 `tests/memory/test_secret_shapes.py`, 两个方向各钉一半", 那个文件不存在且全 git 历史里从未存在. 现有覆盖只有 `tests/memory/test_memory_prompt_and_tools.py:227` 一条端到端断言. 2026-08-28 修的两个误报 (`_TOKEN` 去掉 `/`, `_is_random_token` 的连排规则) 因此没有回归用例守着 |
| 一批 docstring 还在说 REPL / 终端 / 斜杠命令 | 文档漂移. `agent_turn/cancellation.py`, `interfaces/runtime/diagnostics.py`, `application/project/project.py`, `application/agent_run/` 几处都有. 代码行为以实现为准 |
| `docs/02-detailed-design.md` §3.5 列了六种模式与一个 `ModePolicy` 类 | 文档漂移. 以四档 (`PLAN`/`ACCEPT_EDITS`/`AUTO`/`FULL_ACCESS`) 为准, `ModePolicy` 已随 ADR-0009 废弃删除 |
| `docs/01-overview-design.md` §5.1 提到 MCP SDK 与 `ModePolicy` | 同上 |
| `docs/使用相关.md` 的日志表还写着 `/config` 与 `/diagnostics` | 文档漂移, 配置项本身没变, 入口改成了设置页 |

已经补上或已经删掉, 旧材料里仍写着别的说法的 (看到别再当缺口):

| 曾经的说法 | 现状 |
|---|---|
| `forge cli` 进终端 REPL, `#` 开人工 Shell | **两者都不存在了.** ADR-0025 决策 1 修订二 (2026-08-29), 48 个模块 6627 行删除 |
| 斜杠命令 (`/config`, `/models`, `/status`, `/resume`, `/tools`, `/undo`, `/diagnostics`...) | 全部改成 Web 路由与设置页. `application/slash_commands/` 与 `IntentRouter` 已删 |
| `ManualMutationBarrier` (ADR-0017 §10) | 已删. 唯一 trip 方是人工 Shell 服务; 人工 Shell 没了, 它就是一个永远为假的标志位 —— 不会响的安全阀比没有安全阀更糟 |
| `InputOrigin.TTY_USER` | 已删. 没有代码路径能再产出它. 老 `events.jsonl` 里的 `"tty_user"` 字符串不受影响 —— 没有地方把 origin 解析回枚举 |
| `interfaces/web/static/` 在库里 | 已出库. `make package` 依赖 `make web-build`, 打包前现生成 (2026-08-30). 装 wheel 的用户不受影响, 产物仍在包里 |
| plan 档没有计划评审 | 已实现. `application/planning/plan_review.py`, ADR-0023 + ADR-0038 |
| 没有 `plan_read` / `todo_write` 等工具 | 已实现. `application/tools/builtin/planning_tools.py`, 五个 |
| 沙箱恒为 `NO_SANDBOX` | 已实现. Seatbelt / bubblewrap / WSL2 三个 Provider + 启动自测, ADR-0030 |
| 没有上下文压缩 | 已实现. ADR-0032 + ADR-0037 |
| 没有跨会话记忆 | 已实现. ADR-0033 |
| 没有结构化日志 | 已实现. ADR-0035 |
| `git_read` 靠一张 git CLI 参数白名单保证只读 | 已换成 libgit2 进程内调用, 白名单删除, 不再声明 SPAWN_PROCESS. ADR-0040 决策 4.4 |
| 日志开关是 `FORGE_LOG_*` 环境变量 (ADR-0035 决策 3 原文) | 已改成 `logging.*` 配置项, 见 ADR-0035 的 2026-08-28 修订段 |
| `telemetry.enabled` 没有任何消费者 | 已接上: 它现在控制 `shared/observability/metrics.py` 的采集, 默认关 |
| `ToolAuditSink.approval_event` | 已删 (2026-08-30). 零个生产调用点; 审批的审计走 `policy_decision` |

**冲突时的裁定顺序**: 已接受的 ADR > 最新日期的 roadmap > `02-detailed-design.md`.
见 `docs/05-acceptance-standards.md` §9. 同一主题有多份 ADR 时以编号大的为准; 同一份 ADR
有多次修订时以最后一次为准 (ADR-0025 的两次修订是这条的现成例子), 并注意 §1.3 那张替代
关系表.

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
`ExecutionAuthorization`, 不写 learned rules. "同意并执行"只是把 PLAN 升到 AUTO, 与在
模式菜单里手选 auto 完全等价; 升档之后每一步仍然逐次走完整条管线.

**"能力声明可以证明工具安全"** —— 不能. `declared_capabilities` 是**上界**, 只用于目录
过滤和一致性校验. 声明只能缩小信任, 不能证明安全. 读 ADR-0004 §3.

**"记忆是模型说了算的, 那它能影响裁决吗"** —— 不能, 而且这条边界是机器守的.
`check_arch.py` 有一条 `application.memory` 不得 import `application.security` 的
SIBLING_BAN —— 记忆之所以敢做静默写入, 全部承重就在这一条上. 读 ADR-0033 决策 3.

**"Web 只是给终端套了个壳"** —— 反过来了. 终端入口已经整棵删除 (ADR-0025 决策 1 修订二),
浏览器是唯一的业务交互面. `interfaces/` 现在只负责起进程与装配; 一次 turn 跑在
`ProjectRuntime` 起的后台线程里, 页面靠 SSE 看进度, 靠 `POST /api/v1/approvals/{id}/resolve`
解开阻塞在工具线程上的审批.

**"没有终端入口, SSH 上就用不了"** —— 用得了, 但要自己转发端口. ADR-0025 修订二把这条
代价写进了决策正文: 远程会话需要 `ssh -L 8765:127.0.0.1:8765`. 而 `#` 人工 Shell 确实
没有替代品 —— 要在工作区里亲手敲命令, 自己开一个终端.

## 10. 一份两周的日程建议

| 天 | 内容 |
|---|---|
| 1 | 第 1 节建立骨架 + 跑起来 (练习 1) |
| 2-3 | 主线一 (对话链路, 含 §2.1 取消与审批) |
| 4-6 | 主线二 3.1 值对象 + 3.2 管线. **慢一点, 这是核心** |
| 7 | 主线二 3.3 工具实现 + 练习 2 |
| 8-9 | 主线三 4.1 解析 + 4.2 分析器 |
| 10 | 主线三 4.3 围栏 + 练习 7 |
| 11 | 练习 3-6, 8, 把不变量摸一遍 |
| 12-14 | 按当前任务挑支线深入 |

## 关联文档

- `docs/01-overview-design.md` —— 概要设计与分层职责 (注意 §5.1 提到的 MCP 与
  `ModePolicy` 尚未 / 不再存在)
- `docs/02-detailed-design.md` —— 领域模型与事件 schema (注意 §3.5 已漂移)
- `docs/04-engineering-standards.md` —— 命名, 提交, 测试规范
- `docs/05-acceptance-standards.md` —— 验收标准与文档一致性要求
- `docs/adr/README.md` —— 41 份架构决策记录
- `docs/adr/2026-08-19-0025-采用本地Web控制面替代终端交互.md` —— **两次修订都要读**,
  尤其是修订二的"为什么推翻上一次修订"
- `docs/SYNC-TO-MAIN.md` —— 副本到主仓的回流记录
- `AGENTS.md` —— 命名约定与禁用名
- `README.md` —— 从源码跑起来的四步 (`make web-build` 不能省)
- `src/forgecli/application/prompt/templates/README.md` —— 模型可读正文的组织与改动纪律
