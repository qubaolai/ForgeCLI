# 代码阅读路线

给第一次通读这个代码库的人. 目标不是"读完所有文件", 而是**能独立回答"这次工具调用为什么
被拦下来了"**这类问题.

## 0. 这份文档怎么用

代码规模 (2026-08-18):

| 层 | 文件 | 行数 | 说明 |
|---|---|---|---|
| `shared` | 5 | 178 | 取消令牌, JSON Schema 校验, 日志与指标 (ADR-0035) |
| `domain` | 80 | 8940 | 纯值对象, 无 I/O |
| `application` | 122 | 15342 | 用例编排与端口 |
| `infrastructure` | 40 | 3444 | 适配器 |
| `interfaces` | 46 | 5647 | CLI 与组合根 |
| `tests` | 43 | 7939 | 542 个用例 |

**不要按目录顺序读.** 三万行按字母序读完也建立不起图景. 正确的方式是**跟着一次真实请求
走完整条链路**, 中途遇到不懂的值对象再回头翻.

路线分三段:

1. **建立骨架** (约半天): 四份材料, 读完知道"东西大致在哪".
2. **走通三条主线** (约三天): 跟着请求走完三条链路. 这是主体.
3. **按需深入支线**: 用到哪块看哪块.

每一站都给出: 读什么文件 / 配套哪份 ADR / **读完应该能回答什么问题**. 最后一项是自检 ——
答不上来说明这一站没读透, 往下走会越来越吃力.

### 0.1 让运行日志替你走一遍

"跟着一次真实请求走"可以不只是比喻. 开着 debug 跑一句话, 然后读那份日志 (ADR-0035):

```bash
FORGE_LOG_LEVEL=debug poetry run forge cli
tail -f ~/.forge/logs/forge-latest.log
```

一次带工具调用的对话会按发生顺序留下 `turn.received` -> `prompt.compiled` ->
`model.request` -> `model.response` -> `tool.requested` -> `pipeline.prepared` ->
`pipeline.decision` -> `tool.execute.ok` -> `tool.observed` -> `loop.stop`. 每一行的
模块路径就是文件路径 (去掉 `forgecli.` 前缀), 照着 open 即可.

比通读快的地方在于**顺序是真的**: 文档里的链路图是作者整理过的, 日志里的是这台机器上
刚刚实际发生的, 包括那些文档没写的分支 (重试, 降级, 被闸拦下的调用).

## 1. 建立骨架

按顺序读这四份, 不要跳:

### 1.1 `scripts/check_arch.py` (约 90 行)

**第一个读它, 而不是任何架构文档.** 它是这个项目唯一一份**可执行的**架构说明: 三组规则
写成断言, 违反就在 `make ci` 停下.

三组规则分别回答:

- `LAYER_BANS`: 谁不许 import 谁 (domain 不 import 任何内部层; application 不 import
  infrastructure 与 interfaces).
- `BANNED_THIRD_PARTY`: domain 与 application 不许碰 rich, prompt_toolkit, httpx,
  openai, anthropic —— 这条决定了"业务逻辑长什么样".
- `SIBLING_BANS`: 工具层与安全层互相不可见; 人工 Shell 与工具/安全/协调器互相不可见.

它的模块 docstring 解释了为什么要写成断言而不是写进文档: 分层违规都是"能跑通"的, import
一个下层模块永远不会报错, 只会在半年后变成一团解不开的环.

**读完能回答**: 为什么 `application/tools` 里没有任何 `import ...security`?

### 1.2 `AGENTS.md` + `docs/01-overview-design.md` §5.1

命名约定, 分层职责, 禁用名 (`AgentWorkflow` / `BuiltinWorkflow` / `WorkflowResult`, 见
ADR-0003).

### 1.3 `docs/adr/README.md`

25 份 ADR 的索引与状态. **不要现在全读**, 只记住有这么一批东西, 以及"废弃"的三份
(ADR-0003, ADR-0009 已废弃; ADR-0014 的 Provider 选型被 ADR-0019 取代).

### 1.4 `src/forgecli` 的目录树

```text
domain/          纯值对象. 认识这里的名字就认识了整个系统的词汇
  agent/         循环动作, 停止原因, 运行事件, 提示词
  tool/          工具契约: ToolSpec / ToolPlan / Capability / 授权信封
  security/      安全词汇 + Shell 解析 (shell/ 子包)
  execution/     执行画像与环境净化
  recovery/      恢复检查点与变更集
  session/       会话事件与快照
  model/         LLM 请求/响应/流式
application/     用例编排. 端口 (ABC) 也在这里
  agent_loop/    ReAct 循环
  agent_turn/    唯一执行副作用的驱动方
  tool_request/  工具请求协调器 (安全管线的编排者)
  tools/         工具机制层 + builtin/ 九个内置工具
  security/      安全策略层 (分析器, 策略引擎, 分类器)
  recovery/      恢复事务
  prompt/        系统提示词编译
  llm/           LLM 网关
infrastructure/  适配器: 文件系统, 子进程, TOML, HTTP
interfaces/cli/  Typer 入口, REPL, 终端渲染, 组合根
```

**读完能回答**: `ToolPlan` 应该在哪一层? 为什么?

## 2. 主线一: 一次对话是怎么跑完的

从用户敲下一行字, 到终端出现回复. 这条线最短, 先走它建立"请求在系统里怎么流动"的感觉.

| # | 文件 | 看什么 | 读完能回答 |
|---|---|---|---|
| 1 | `interfaces/cli/app.py` | Typer 入口, `main()` | 裸 `forge` 为什么进 REPL 而不是打印帮助? |
| 2 | `interfaces/cli/bootstrap.py::run()` | **组合根**. 从 banner 到 `Repl.run()` 的完整装配 | 装配顺序里哪几步是安全约束, 换顺序会怎样? |
| 3 | `application/intent_router.py::IntentRouter.route` | 一行输入被识别成什么意图 | `#` 开头为什么只在特定条件下才算人工 Shell? |
| 4 | `domain/intents.py` | `SessionMode`, `InputOrigin`, `UserIntent` 家族 | `ManualShellIntent` 的构造函数为什么强制 origin? |
| 5 | `interfaces/cli/repl.py::Repl._dispatch` | 意图分派 | Ctrl-C 为什么不抛异常而是设 CancelToken? |
| 6 | `application/agent_turn/agent_turn_service.py` | `handle_user_message` -> `_run_loop` -> `_outcome_from_stop` | 为什么说它是"唯一执行副作用的 application service"? |
| 7 | `application/agent_loop/loop.py` | `AgentLoop` 端口: `start` / `observe` | 为什么循环只有这两个方法? |
| 8 | `application/agent_loop/builtin_loop.py` | ReAct 主体 (785 行, 分两次读) | 模型一次要三个工具时会发生什么? |
| 9 | `domain/agent/actions.py` | `LoopAction` / `LoopObservation` / `ObservationDisposition` | `is_error` 与 `disposition` 为什么是两个字段? |
| 10 | `domain/agent/stop.py` | `LoopStopReason` 与分类 | 文件末尾那个 `assert` 挡住了什么? |
| 11 | `domain/agent/run_events.py` | 20 种运行事件与它们的 payload | 为什么每种事件一个 frozen 类而不是自由 dict? |
| 12 | `interfaces/cli/run_renderer.py` | 终端唯一写入者 | 为什么正文要按 `request_id` 分块? |

**配套 ADR**: 0010 (循环架构), 0016 (运行事件流), 0007 (斜杠命令).

**这一段的核心不变量**: `AgentLoop` 只产出意图 (`LoopDecision` / `LoopAction` /
`LoopStop`), 不执行任何副作用. 副作用全部由 `AgentTurnService` 执行. 读第 8 站时刻意
验证这一点 —— 循环里拿不到 `ToolRegistry`, 也拿不到 `ToolRuntime`.

## 3. 主线二: 一次工具调用是怎么被裁决的

**这是整个项目最核心的部分**, 也是最难的. 预留最多时间.

先读契约, 再读管线, 最后读工具实现.

### 3.1 契约层 (`domain/tool/`)

按这个顺序:

1. `capability.py` —— 15 个能力的闭集. 整套解耦的基础.
2. `spec.py` —— `ToolSpec`: 能力**上界**, 不是放行证明. 注意它**没有**
   `requires_authorization` 也**没有** `risk_level`, docstring 解释了为什么.
3. `plan.py` —— `ToolPlan`: 工具与安全之间**唯一**的事实载体. 这是全项目最重要的一个
   值对象, 值得读两遍. 注意 `TargetResolution` 与 `DeclarationConfidence` 的区别.
4. `authorization.py` —— `ExecutionAuthorization`: 不透明凭证, 单次使用. 看
   `validate_narrowing` —— 安全模块只能**收缩**计划, 不能放宽.
5. `errors.py` / `result.py` / `catalog.py` / `hashing.py` —— 快速过一遍.

**读完能回答**: 为什么"新增工具不需要修改安全模块"? 这句话靠什么机制成立?

### 3.2 管线 (`application/tool_request/` + `application/security/`)

`application/tool_request/coordinator.py::ToolRequestCoordinator.handle` 是整条管线的
主干 (663 行). **先只读 `handle` 本身**, 把它调用的每个私有方法当黑盒, 建立顺序感:

```text
bind_turn -> catalog_for(mode) -> _check_availability -> _prepare
  -> ToolAuthorizationService.evaluate
  -> DENY / ASK / ALLOW 三条分支
  -> (ASK 时) 阻塞审批 -> _revalidate 重新 prepare + 重新裁决 + 比对绑定哈希
  -> 恢复事务 -> authorization.issue -> 写前审计 -> ToolRuntime.execute
```

然后逐个展开:

| 文件 | 职责 |
|---|---|
| `tool_request/catalog_predicates.py` | mode 能力门. **只有 20 行**, 但它是 plan 档的全部实现 |
| `security/authorization_service.py` | 按能力分派分析器, 校验收缩, 交给策略引擎 |
| `security/analyzers/registry.py` | 分析器注册表. 按 `Capability` 分派, **不认识工具名** |
| `security/policy_engine.py` | 最终裁决. `DENY > MANDATORY ASK > ASK > ALLOW` |
| `domain/security/modes.py` | 四档模式的能力预算. 只有"自动允许"表, 没有 deny 表 |
| `domain/security/protected_paths.py` | Hard Deny 的路径判据. 只看 realpath |
| `security/learned_rules.py` | `always` 规则. 注意它落盘, 且跨进程比对 |
| `tools/runtime.py::ToolRuntime.execute` | 唯一执行入口, 授权为空即拒 |

**配套 ADR**: 0004 (工具系统), 0013 (分层 Shell 安全), 0020 (LLM 补充评估),
**0021 (失败向安全的默认值)** —— 0021 记录了一轮审计发现的系统性缺陷, 读它能理解这套
设计在防什么.

**读完能回答**:
- 人类批准之后为什么还要重新 prepare 一遍?
- `plan_hash` 里为什么不含 `filesystem_view_version`?
- plan 档下模型硬要调 `shell_run` 会发生什么? 有几道防线?

### 3.3 工具实现 (`application/tools/`)

先读 `tool.py` (Tool ABC) 和 `builtin/base.py` (共享助手), 然后按**从简到繁**:

```text
plan_update.py    最简单. PLAN_ONLY, 无副作用, 30 秒读完
fs_read_file.py   看它怎么按调用现场决定是 WORKSPACE_READ 还是 EXTERNAL_READ
fs_list_files.py  看 EXPANDABLE: 在 prepare 里把 glob 展开成封闭集合
search_text.py    看它为什么不 shell out 到 grep
git_read.py       看白名单为什么是白名单而不是禁止清单 (docstring 有完整论证)
fs_write.py       看"唯一性是硬约束"那段, 以及为什么不允许全文覆盖
shell_run.py      最复杂. OPAQUE + UNKNOWN, 12 个能力上界
```

**读完能回答**: 同样是读文件, 为什么 `fs_read` 在 plan 档可见而 `shell_run` 不可见?

## 4. 主线三: 一条 Shell 命令是怎么被分析的

这条线可以晚一点走, 但不能不走 —— 它是 `shell_run` 能存在的全部理由.

| # | 文件 | 看什么 |
|---|---|---|
| 1 | `domain/security/shell/tokens.py` | 分词 |
| 2 | `domain/security/shell/parser.py` | 按方言分派 (posix / cmd / powershell) |
| 3 | `domain/security/shell/posix.py` | 完整的 POSIX 语法模型 |
| 4 | `domain/security/shell/command_plan.py` | 解析产物: `CommandPlan` |
| 5 | `domain/security/shell/wrappers.py` | 548 行, 全项目最烧脑的一份. 剥 `sudo` / `env` / `xargs` / `bash -c` 直到真实命令 |
| 6 | `domain/security/shell/effects.py` | 命令 -> 文件系统影响. **表外默认值是 `UNPROVEN`** |
| 7 | `application/security/analyzers/shell_analyzer.py` | 把上面的产物变成能力与 effects |
| 8 | `application/security/analyzers/script_analyzer.py` | 脚本路径: 静态信号 + LLM 分类器 |
| 9 | `application/security/classifier.py` | 分类器与 fail-safe 包装 |
| 10 | `domain/security/risk.py` | `RiskReport` 与缓存键 |

**配套 ADR**: 0013 §3/§6/§7 (解析), §10/§11 (分类器), **§12 (缓存键与版本常量)**.

**读完能回答**:
- `bash -lc 'rm -rf /'` 为什么不会被判成"只是跑了个 bash"?
- 分类器超时了会怎样? 为什么不能"失败就放行"?
- 改了 `_SYSTEM_PROMPT` 却不升 `CLASSIFIER_PROFILE_VERSION` 会有什么后果?

## 5. 支线: 按需深入

主线走完之后, 这几块可以独立读, 互不依赖:

| 支线 | 入口 | 配套 ADR | 什么时候读 |
|---|---|---|---|
| 工作区恢复 | `application/recovery/coordinator.py` | 0015 | 想理解 `/undo` 与"首次破坏性写入屏障" |
| 系统提示词 | `application/prompt/system_prompt_builder.py` | 0018 | 想改模型行为 |
| 人工 Shell | `application/manual_shell/service.py` | 0017 | 想理解 `#` 这条独立信任通道 |
| LLM 网关 | `application/llm/gateway/default_gateway.py` (1062 行) | 0011, 0012 | 要接新供应商 |
| 会话存储 | `application/session/session_service.py` | 0001, 0008 | 想理解 `/resume` |
| 终端渲染 | `interfaces/cli/run_renderer.py` | 0016 | 要改终端输出 |
| 配置系统 | `application/config/config_service.py` | 0005, 0008 | 要加配置项 |

## 6. 这个代码库的阅读技巧

**docstring 承载"为什么", 不是"是什么".** 这是这个项目最重要的阅读入口. 大部分模块与
关键方法的 docstring 记录的是**当初为什么这么选, 以及不这么选会怎样**. 例如
`fs_write.py` 里"唯一性是硬约束而不是便利检查"那段, 或者 `hashing.py` 里为什么不能用
`dataclasses.asdict`. **看到长 docstring 不要跳过, 那里面是设计决策.**

**注释里带"曾经"/"早先"/"原来"的地方是踩过的坑.** 全项目搜这几个词, 能捞出一批真实
事故的记录. 例如 `plan.py` 里 `filesystem_view_version` 为什么标 `compare=False`.

**不变量集中在三处**: 值对象的 `__post_init__`, 模块级 `assert` (如 `stop.py` 末尾),
以及 `scripts/check_arch.py`. 想知道"什么是不能违反的", 看这三处.

**`__all__` 是模块的对外契约.** 没进 `__all__` 的名字是内部实现.

**测试是第二份文档.** 测试函数名是完整的英文句子 (`test_a_recursive_delete_expands_to_
every_file`), 读测试名就知道系统承诺了什么. 43 个测试文件按主题分组:

```text
tests/tools/         工具行为
tests/security/      安全裁决与回归. 文件名带 regressions 的都是真实事故
tests/tool_request/  管线接缝
tests/agent_run/     循环与事件时间线
tests/prompt/        提示词编译
tests/manual_shell/  人工 Shell 的信任边界
tests/commands/      斜杠命令
tests/support/       共享替身
```

## 7. 用改动验证理解

读懂的标准不是"看完了", 是"能预测改动的后果". 建议按顺序做这几个练习:

1. **跑起来**: `make ci`, 然后 `poetry run forge`. 看一次真实对话的终端输出.
2. **加一个最小工具**: 照 `plan_update.py` 写一个 `echo.text`, 注册进
   `tool_wiring.py`. 验证: 它自动出现在 `/tools` 里, 且在四档模式下的可见性符合你的预期.
3. **故意违反分层**: 在 `domain/tool/plan.py` 里 `import rich`, 跑 `make arch`. 看它
   怎么拦你.
4. **故意破坏不变量**: 把 `fs.delete` 的 `target_declaration_ability` 改成 `STATIC`,
   跑 `tests/tools/test_fs_write_and_delete.py`. 这是真实发生过的一个缺陷.
5. **改内置提示词**: 改一个字, 跑 `tests/prompt/`. 看快照测试怎么逼你升版本号.
6. **读一条完整审计**: 跑一次带工具的对话, 然后看
   `~/.forge/projects/<id>/sessions/<id>/events.jsonl`. 把事件序列和你读的代码对上.

## 8. 当前已知的缺口

**这些地方不要浪费时间困惑** —— 它们是有意为之或尚未落地, 不是你读漏了:

| 现象 | 状态 |
|---|---|
| `LoopState` 从未被实例化 | ADR-0010 的契约, 尚无 mid-turn resume, 是预留 |
| `LoopHook` 从未被调用 | 同上, 控制流扩展点未启用 |
| `LoopInput.resume_state` 恒为 None | 同上 |
| `plan` 档没有计划评审 (补充/拒绝/同意/同意并执行) | ADR-0023 已设计, 未实现 |
| 没有 `plan_read` / `todo_write` 等工具 | ADR-0022 已设计, 未实现 |
| 沙箱恒为 `NO_SANDBOX` | ADR-0014 / 0019 已设计, 未实现. `IsolationLevel` 三档是为它预留 |
| `SessionMode` 不落盘 | 刻意的: `state.json` 必须能从 `events.jsonl` 重建, 而 mode 没有对应事件 |
| `docs/02-detailed-design.md` §3.5 列了六种模式 | 文档漂移. 以四档 (`PLAN`/`ACCEPT_EDITS`/`AUTO`/`FULL_ACCESS`) 为准 |

**冲突时的裁定顺序**: 已接受的 ADR > 最新日期的 roadmap > `02-detailed-design.md`.
见 `docs/05-acceptance-standards.md` §9.

## 9. 常见误解

**"提示词能约束模型行为"** —— 不能. 提示词只降低无效请求. 真实边界是 Tool Catalog,
Policy Engine, ApprovalService 与 ToolRuntime. 读 ADR-0018 §9.1.

**"审批通过就是授权"** —— 不是. 批准之后必须重新 prepare, 重新裁决, 重验约十五项绑定
事实, 才能签发授权. 读 ADR-0004 §6.1.

**"分类器说安全就能跑"** —— 不是. 分类器只产出事实, 裁决由本地策略引擎作出, 而且
Hard Deny 在分类器**之前**. 读 ADR-0020.

**"缓存命中就跳过检查"** —— 不是. 缓存省掉的只是分类器那一次 LLM 调用, Hard Deny 与
当前策略检查照常执行. 读 ADR-0013 §12.

**"能力声明可以证明工具安全"** —— 不能. `declared_capabilities` 是**上界**, 只用于目录
过滤和完整性校验. 声明只能缩小信任, 不能证明安全. 读 ADR-0004 §3.

## 10. 一份两周的日程建议

| 天 | 内容 |
|---|---|
| 1 | 第 1 节建立骨架 + 跑起来 (练习 1) |
| 2-3 | 主线一 (对话链路) |
| 4-6 | 主线二 3.1 契约层 + 3.2 管线. **慢一点, 这是核心** |
| 7 | 主线二 3.3 工具实现 + 练习 2 |
| 8-9 | 主线三 (Shell 分析) |
| 10 | 练习 3-6, 把不变量摸一遍 |
| 11-14 | 按当前任务挑支线深入 |

## 关联文档

- `docs/01-overview-design.md` —— 概要设计与分层职责
- `docs/02-detailed-design.md` —— 领域模型与事件 schema (注意 §3.5 已漂移)
- `docs/04-engineering-standards.md` —— 命名, 提交, 测试规范
- `docs/05-acceptance-standards.md` —— 验收标准与文档一致性要求
- `docs/adr/README.md` —— 25 份架构决策记录
- `docs/SYNC-TO-MAIN.md` —— 副本到主仓的回流记录, 含一份"代码里已知未实现的"清单
