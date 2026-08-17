# 回流清单: claude_test -> ForgeCLI

对照 `diff -rq src/forgecli <主仓>/src/forgecli` 生成, 覆盖 ADR-0004 / 0013 / 0014(非沙箱条款) /
0015 / 0016 / 0017 六份 ADR 的全部实现.

规模: **新增 121 个源文件, 修改 34 个, 删除 3 个文件 + 1 个空目录**; 另有 31 个测试文件与构建配置.
副本当前 `make ci` 全绿, **379 个测试**.

---

## 先修主仓: `forgecli.domain.tool` 现在 import 就炸

在开始搬之前先跑这条, 它现在**必然失败**:

```bash
cd <主仓> && PYTHONPATH=src python3 -c "import forgecli.domain.tool"
```

```
ImportError: cannot import name 'ToolSpec' from 'forgecli.domain.tool.tool_call'
```

原因: `tool_call.py` 里的类已经改名成 `ToolSchema` (见组 2), 但 `domain/tool/__init__.py`
第 3 行还写着 `from forgecli.domain.tool.tool_call import ToolCall, ToolSpec`. 整个
`domain.tool` 包在主仓是 import 不进去的, 于是**任何依赖它的东西都跑不起来**, 也包括你想用来
验证同步结果的 `make ci`.

同一批手工录入的四个文件还有两处需要一并处理:

| 文件 | 问题 | 处理 |
|---|---|---|
| `domain/tool/__init__.py` | 导出的是已改名的 `ToolSpec` | 用副本版本覆盖 |
| `domain/tool/plan.py` | 第 20 行 `from turtle import st` —— IDE 自动补全误插, 与 `from pickle import NONE` 同一类事故 | 用副本版本覆盖 |
| `domain/tool/spec.py` | `from typing import Mapping` (已废弃位置), import 顺序不符合 ruff isort | 用副本版本覆盖 |
| `domain/tool/hashing.py` | `_canonical` 里手写 append 循环, 文件末尾缺换行 | 用副本版本覆盖 |

这四个文件在主仓已经存在但是**手工重打过一遍**的版本, 措辞与实现都有出入. 建议直接用副本覆盖,
不要逐行对比合并 —— 它们是组 2 的一部分, 与 `catalog.py` / `authorization.py` / `result.py` /
`errors.py` 一起才自洽.

---

## 怎么用这份清单

按下面 16 组的顺序同步. 组的顺序就是**依赖方向** (shared -> domain -> application ->
infrastructure -> interfaces), 照着走每一步都能 `poetry run mypy` 过.

每组标了三件事:

- **为什么存在** —— 这组代码解决什么问题. 同步时最该读的就是这段.
- **整组同步** 标记 —— 带这个标记的组内部互相 import, 拆开搬会断; 不带的可以逐个搬.
- **注意** —— 手工同步时容易踩的地方.

组 1 到 9 基本是纯新增 (主仓根本没有这些包), 搬过去不会影响现有代码.
组 10 之后开始改动既有文件, 风险集中在这里.

---

## 组 1 · 共享内核与构建配置

**为什么存在**: `CancelToken` 原来住在 `domain/model/request.py` 里, 但工具执行 (ADR-0004 §10)
也要用它 —— 留在那儿会让工具系统反向 import `domain.model`. 上移到 `shared` 是为了让两边都能
依赖它而不互相依赖. `json_schema` 同理: 原来是 LLM 网关内部的 schema 校验, 现在工具入参校验
(`tools/builtin/base.py`) 也要用, 于是上移到 `shared`.

| 文件 | 动作 | 说明 |
|---|---|---|
| `src/forgecli/shared/cancellation.py` | 修改 | `CancelToken`. 按身份比较的可变实体, 表达"这次在途调用被叫停了" |
| `src/forgecli/shared/json_schema.py` | 修改 | 最小 JSON Schema 校验. 由网关内部上移, 供网关与工具共用 |
| `scripts/check_arch.py` | 新增 | 依赖方向静态检查 (见组 16) |
| `Makefile` | 修改 | 加 `arch` 目标并入 `ci` |
| `pyproject.toml` | 修改 | `pythonpath` 加 `tests` (共享 fake 要能被各子目录 import) |

**注意**: `pyproject.toml` 主仓的 httpx 版本约束是 `>=0.28.1,<0.29.0`, 副本是 `>=0.28,<0.29`;
`PIP_INSTALL_ARGS` 主仓多一个 `--break-system-packages`. **这两处保留主仓的值**, 是环境差异不是改动.

---

## 组 2 · 工具契约层 (ADR-0004)

**整组同步.**

**为什么存在**: 这是工具系统与安全系统之间**唯一**的共享词汇. 两边都只认识这一层, 互相不认识
对方 —— 安全模块不知道有 `shell.run` 这个东西, 工具模块不知道有"审批"这回事. 唯一的装配点是
组 9.

三条口径写在 `spec.py` 的模块 docstring 里, 同步时值得先读:
spec 声明的是**能力上界不是放行证明**; 没有 `requires_authorization`; 没有 `risk_level`.

| 文件 | 动作 | 说明 |
|---|---|---|
| `domain/tool/capability.py` | 修改 | `Capability` 闭集 15 值 + 词汇表版本. 无法识别的声明归一为 `UNKNOWN`, 不跳过 |
| `domain/tool/spec.py` | 修改 | `ToolSpec` 全字段 + `spec_hash`. MCP 工具强制 `untrusted` + `opaque`. **无 `side_effect_class`** —— resume 不重放, 那个声明没有消费方 |
| `domain/tool/plan.py` | 修改 | `ToolPlan` —— **唯一的事实载体**. `PlanEffects` / `TargetResolution` / `AnalysisSubject` 密封层次 |
| `domain/tool/hashing.py` | 修改 | 规范化 JSON + sha256. 四个 hash 都出自这里 |
| `domain/tool/authorization.py` | 新增 | `ExecutionAuthorization` 不透明信封 + `validate_narrowing` |
| `domain/tool/result.py` | 新增 | `ToolResult`. 内容块带 `provenance=tool_output` / `trust=untrusted` |
| `domain/tool/errors.py` | 新增 | `PreparationError` 与错误码 |
| `domain/tool/catalog.py` | 新增 | `CatalogQuery` -> `ToolCatalog`. 注册表不认识 mode |
| `domain/tool/__init__.py` | 修改 | 导出上面这些. **主仓当前这个文件是坏的**, 见开头 |

**注意**: `tool_call.py` 的 `ToolSpec` -> `ToolSchema` 改名主仓已经做过了, 但 `__init__.py` 没跟上.
波及的 5 个消费点 (`domain/model/request.py`, `domain/agent/state.py`,
`application/llm/gateway/{provider,token_estimator,__init__}.py`) 主仓也已同步 —— 只剩
`__init__.py` 这一处.

---

## 组 3 · 执行画像与环境净化 (ADR-0014 非沙箱条款)

**为什么存在**: 沙箱本次不实现, 但这几条与沙箱无关 —— 它们是"授权时看到的 `python3` 与真正跑
起来的 `python3` 是同一个"的保证. 一个能写工作区的 Agent 只要往 `.envrc` 或 `PYTHONPATH` 里放
点东西, 下一条"看起来安全"的命令就会执行它写进去的代码.

| 文件 | 说明 |
|---|---|
| `domain/execution/environment.py` | 环境净化 allowlist + 注入变量黑名单 + `ShellLaunch`. 纯函数, 不读 `os.environ` |
| `domain/execution/profile.py` | `ExecutionProfile` + `execution_profile_hash` + `IsolationLevel` |
| `infrastructure/execution/environment_probe.py` | 按平台构造受控 PATH 与净化后环境 |
| `infrastructure/execution/local_command_executor.py` | `CommandExecutor` 的唯一实现. 非交互, 非登录, 不加载 profile/rc |

**注意**: `IsolationLevel` 保留三档但只产出 `NO_SANDBOX`. 这**不是**占位癖 —— 它是授权信封的
绑定项, 将来接任何隔离方案时旧授权应当因为这一项变化而自动失效, 而不是靠有人记得清缓存.

---

## 组 4 · Shell 解析 (ADR-0013 §3 / §6 / §7)

**整组同步.** 纯函数, 无 IO, 不依赖组 2 之外的任何东西.

**为什么存在**: `cat a.txt | grep b && rm -rf /` 必须在**任何子进程启动前**整体被拒. 做到这一点
的前提是把原始命令解析成 AST 再逐段预检, 而不是拿正则去匹配命令名.

| 文件 | 说明 |
|---|---|
| `domain/security/shell/tokens.py` | 词法层. 嵌套结构替换成不会出现在真实路径里的占位符 |
| `domain/security/shell/command_plan.py` | `CommandPlan` / `CommandUnit` / `ParseStatus`. 解析失败标 `OPAQUE`, 绝不自动 Allow |
| `domain/security/shell/posix.py` | POSIX 方言: 连接符, 子 shell, 命令/进程替换, 重定向, heredoc |
| `domain/security/shell/cmd.py` | Windows cmd: `%VAR%`, 延迟变量, `^` 转义 |
| `domain/security/shell/powershell.py` | PowerShell: 对象管道, 脚本块, `-EncodedCommand` (解码后分析, **不执行**) |
| `domain/security/shell/wrappers.py` | 递归剥离 `sh -c` / `xargs` / `find -exec` 等包装, 带深度上限 |
| `domain/security/shell/expansion.py` | 受控 glob 与变量展开. 展不开的返回 `DYNAMIC`/`UNKNOWN` |
| `domain/security/shell/builtins.py` | **本轮新增**. 按方言的内建命令表 + `dialect_has_closed_builtin_set()` |
| `domain/security/shell/parser.py` | 方言分派入口 |

**注意**:
- `expansion.py` 里有个易漏的点 —— `~` 必须用**冻结环境快照**里的 HOME 展开.
  `cat ~/.ssh/id_rsa` 曾经因为没展开 `~` 而被判成工作区内路径.
- `builtins.py` 里 `dialect_has_closed_builtin_set()` 不是可有可无的包装. POSIX 与 cmd 的内建
  集合有限可枚举, PowerShell 的 cmdlet 上千且能动态注册 —— **枚举不出来就不许下结论**.
  少了这个判据, 组 8 的 `EXECUTABLE_NOT_FOUND` 会把整个 PowerShell 判死.
- 大小写: cmd 不区分, POSIX 区分. 两边不能共用一套比较.

---

## 组 5 · 安全词汇, 规则与受保护路径 (ADR-0013 §4 / §5 / §8 / §14)

**整组同步.**

| 文件 | 说明 |
|---|---|
| `domain/security/vocabulary.py` | `Decision` / `RuleKind` / `DecisionReason` 码表 / `ApprovalScope` |
| `domain/security/modes.py` | 四种 mode 的能力预算矩阵 |
| `domain/security/hard_deny.py` | Hard Deny 底线 + **不依赖完整 AST** 的预扫描 |
| `domain/security/rules.py` | 结构化规则. 只作用于能力/effects/身份/目标集合, **不作用于工具名或命令名** |
| `domain/security/protected_paths.py` | `ProtectedPathPolicy`. 区分 forge runtime root 与"用户打开的 Forge 源码仓库" |
| `domain/security/executable_identity.py` | 可执行文件身份: realpath + 文件身份 + 内容 hash + 解释器链 |
| `domain/security/script_facts.py` / `script_patterns.py` | ADR-0013 §8 的 13 项脚本事实与确定性静态分析 |
| `domain/security/risk.py` | `RiskReport` / `AgentRiskResponse` / 分类器输出 schema |
| `domain/security/decision.py` | `AuthorizationDecision` |
| `domain/security/context.py` | `PolicyContext` |
| `domain/security/approval.py` | `ApprovalPresentation` (完整事实) + `ApprovalBinding` + `HitlApprovalView` (人类界面 DTO) |
| `infrastructure/security/protected_paths_builder.py` | 按平台生成受保护根: 45 条凭证子路径 (deny_read) + 24 条启动项 (deny_write) + 平台系统目录与设备节点 |

**注意**:

- `approval.py` 里 `ApprovalPresentation` 与 `HitlApprovalView` **是两个东西**, 别合并.
  前者是授权重验需要的完整事实, 后者是给人看的减法视图 (ADR-0016 §8.4). 减掉的是 hash, 不是效果.
- `protected_paths_builder.py` 里 `_home_roots()` **不能改写成 `Path.home()`**. 后者等价于
  `expanduser("~")`, 在 POSIX 上先读 `HOME` 环境变量 —— 一个能设环境变量的调用方把 `HOME` 指到
  空目录, 整张凭证保护表就全部落空. 现在的做法是 passwd 里 real uid 的 home + `SUDO_USER` 的
  home + 环境变量 home 三者取并集.
- 同理 `_other_user_roots()` 扫的是 `_HOME_CONTAINERS` (`/home` `/Users` `/export/home`
  `/var/home`) 而不是 `home.parent` —— 后者在 `HOME=/tmp/x` 时会把 `/tmp` 当成 home 容器.
- `_merged()` 里重复声明**只能收紧**: 同一路径出现两次时 `deny_read` 取 OR.
- `DecisionReason` 本轮新增了 `EXECUTABLE_NOT_FOUND`, 它**放在 `# -- DENY --` 段但不是
  `HARD_DENY_*`**. 见组 8.

---

## 组 6 · 工作区恢复系统 (ADR-0015)

**整组同步.**

**为什么存在**: 规则引擎判 ALLOW 不等于改坏了能还原. 两层独立 —— 恢复层不参与 mode / 规则匹配 /
人工审批, 也不能把 `ASK`/`DENY` 提升为允许.

| 文件 | 说明 |
|---|---|
| `domain/recovery/checkpoint.py` | `RecoveryCheckpoint` / `TransactionState` / `SnapshotStrategy` |
| `domain/recovery/mutation.py` | `MutationEntry` / `MutationSet` / `Operation` |
| `application/recovery/coordinator.py` | **首次破坏性写入屏障**: 写前日志 -> 存 preimage -> manifest 落盘 -> ARMED -> 才允许真实写入 |
| `application/recovery/recovery_service.py` | list / preview / restore / undo. postimage 冲突检查, 冲突不静默覆盖 |
| `application/recovery/recovery_store.py` | 存储抽象 |
| `infrastructure/recovery/fs_recovery_store.py` | 内容寻址 blob 去重, manifest 原子写 |

**注意**: 恢复数据落在 `~/.forge/state/recovery/`, **必须在工作区之外** —— 一条 `rm -rf .` 不能
同时删掉工作区和用来还原它的备份.

---

## 组 7 · 工具机制层 (ADR-0004 §2 / §14)

**整组同步.** 依赖组 2、3、6.

**为什么存在**: 工具只提供机制 (`describe` -> `prepare` -> `execute`), 不做任何安全决策.
工具实现里出现 mode 判断, 规则匹配, 审批调用或分类器调用, 就是架构违规 (组 16 的脚本会拦).

| 文件 | 说明 |
|---|---|
| `application/tools/tool.py` | `Tool` ABC. 只有 `prepare` 与 `perform` |
| `application/tools/registry.py` | `ToolRegistry`. 按谓词过滤出目录快照, 谓词只看 `ToolSpec` |
| `application/tools/runtime.py` | `ToolRuntime.execute`. **强制授权前置**, 只消费 `effective_plan` 不重读原始入参 |
| `application/tools/resource_governor.py` | 超时, 输出上限, 子进程回收 |
| `application/tools/command_executor.py` | `CommandExecutor` ABC —— 将来接隔离方案的**唯一接缝** |
| `application/tools/artifact_store.py` | 超阈值输出溢写位置. 产物不落工作区 |
| `application/workspace/execution_context.py` | 一次调用被冻结的执行上下文 (消除 TOCTOU) |
| `application/workspace/filesystem_view.py` | 带版本的只读视图 |
| `infrastructure/workspace/os_filesystem_view.py` | 真实文件系统实现 |
| `infrastructure/tools/fs_artifact_store.py` | 内容寻址产物存储 |

内置工具 (`application/tools/builtin/`):

| 文件 | 工具 | 说明 |
|---|---|---|
| `base.py` | — | 入参校验 + `emit_text` 溢写 |
| `plan_update.py` | `plan.update` | 任务计划 |
| `fs_read_file.py` | `fs.read_file` | |
| `fs_list_files.py` | `fs.list_files` | 带 `depth` 与默认忽略规则 (`.git`/`node_modules`/…), **过滤在 prepare 做** |
| `search_text.py` | `search.text` | v2: 正则 (prepare 阶段编译校验) + 上下文行 + 按文件分组 |
| `fs_write.py` | `fs.write_patch` / `fs.delete` | write v2 改**精确替换**; delete v2 支持目录 (展开成逐个文件) |
| `fs_move.py` | `fs.move` | 新增. 两端在 prepare 封闭, 目标已存在时拒绝不覆盖 |
| `git_read.py` | `git.read` | 只读子命令白名单 |
| `shell_run.py` | `shell.run` | 唯一 Shell 入口, 且**不是**安全决策者 |

**注意**: 三处设计决定容易在手工同步时被"顺手改回去", 别改:
1. `fs.write_patch` 的替换在 **prepare** 阶段算完, 计划里存最终内容 —— 裁决绑定的是"文件会变成
   什么样", 执行阶段不必重读文件.
2. `fs.delete` 的目标是**逐个文件**而不是目录名 —— 恢复层按路径存 preimage, 审批界面按路径列清单.
3. `fs.list_files` 的忽略过滤在 **prepare** —— 被过滤的路径不该进 `read_paths`, 否则安全侧要为
   一堆压根不读的文件做判断.

---

## 组 8 · 安全策略层 (ADR-0013)

**整组同步.** 依赖组 4、5.

**为什么存在**: 安全模块**不认识任何具体工具类型**. 分派按**能力**走 —— 任何声明 `EXECUTE_SHELL`
的工具都交出 `ShellSubject`, 分析器只认这个.

| 文件 | 说明 |
|---|---|
| `application/security/authorization_service.py` | `evaluate` / `issue`. fail closed 默认分支 |
| `application/security/policy_engine.py` | `Hard Deny > 跑不了 > Mandatory Ask > 模式预算 > Ask > Allow` |
| `application/security/analyzers/registry.py` | **两阶段分派**: `DERIVE` (shell/script 产出事实) 后 `CHECK` (workspace/network 检查收窄后的事实) |
| `application/security/analyzers/shell_analyzer.py` | 命令 -> `CommandPlan` -> 目标集合; 可执行文件解析与内建命令识别 |
| `application/security/analyzers/script_analyzer.py` | 脚本事实 + 分类器 |
| `application/security/analyzers/workspace_analyzer.py` | 路径归属与受保护路径 |
| `application/security/analyzers/network_analyzer.py` | 网络目标 |
| `application/security/analyzers/unknown_analyzer.py` | 兜底: 认不出来就 ASK |
| `application/security/approval_service.py` | 阻塞式 ASK. 非交互保持 pending, 超时绝不转 Allow |
| `application/security/learned_rules.py` | 渐进式授权: 一次 `always` 沉淀成一条受限 Allow 规则 |
| `application/security/classifier.py` | `SafetyClassifier` ABC + fail-safe 包装 + `FakeSafetyClassifier` |
| `application/security/gateway_classifier.py` | 经 `LlmGateway.complete_structured` 调用, **隔离系统提示** |
| `application/security/risk_cache.py` | ADR-0013 §12 缓存键 |
| `application/security/executable_resolver.py` | 可执行文件解析 |
| `application/security/workspace_grants.py` | `/add-dir` 的读/写分级授权 |
| `application/security/wiring.py` | 分析器注册表装配 |
| `infrastructure/security/toml_learned_rules_store.py` | 学习规则落盘 |

### 本轮新增: "跑不了"与"不许跑"分开

起因是一个真实现象: Windows 上模型不知道自己在哪个平台, 发了 `ls -al`. 受控 PATH 里没有 `ls`,
原来的处理是 ASK —— **Forge 明知这条命令跑不了, 还是弹 HITL 问用户批不批准**, 而批准也没用:
执行用的是同一份受控 PATH, 点了同意照样失败. 一个批准了也没用的请求不该占用一次人类打断.

改动是三处:

| 位置 | 改动 |
|---|---|
| `domain/security/vocabulary.py` | 新增 `DecisionReason.EXECUTABLE_NOT_FOUND` |
| `analyzers/registry.py` | `AnalysisFindings.unrunnable` 字段 + `cannot_run()` 方法, 与 `hard_deny` 并列 |
| `policy_engine.py` | 新增分支, 位置在 Hard Deny **之后**, 其余所有分支**之前** |
| `analyzers/shell_analyzer.py` | `_check_executables` 跳过内建命令; 新增 `_unresolved()` 分派 |

**注意**:
- `EXECUTABLE_NOT_FOUND` **不复用 `HARD_DENY_*`**. 那是"不许跑", 这是"跑不了" —— 混进去会让
  审计里堆一批假的安全拒绝, 而它们其实只是模型用错了平台的命令.
- 分支位置两头都不能挪: 排在 Hard Deny 之后, 是因为一条既危险又跑不了的命令, 审计里该记的是
  安全理由; 排在模式预算之前, 是因为原来出问题的正是那里 (`EXECUTE_SHELL` 在 accept_edits 下
  超预算 -> ASK).
- 拒绝消息带上平台与方言 (`... 在 darwin 的 posix 环境里既不是内建命令, 也不在受控 PATH 上`).
  光说"找不到"模型不知道往哪改; 说了平台它才知道自己发错了方言的命令.
- PowerShell 维持原来的 ASK. 见组 4.
- 身份哈希的分母**不含内建命令**. 不改这一处的话, `export FOO=1 && git push` 这类命令永远凑不齐
  可执行文件身份, 也就永远学不到规则.
- `wiring.py` 里 `cache or RiskCache()` 这种写法是**错的** —— `RiskCache` 有 `__len__`,
  空缓存是 falsy, 注入的缓存会被静默丢弃. 必须写 `RiskCache() if cache is None else cache`.
  `classifier.py` 的 fail-safe 包装也别去掉: "没接分类器"绝不能变成"跳过分类器".
- 学习规则存储**只存哈希**: 命令参数与脚本内容一律不落盘, 唯一的例外是可执行文件**基名**
  (`git` / `npm`) —— 它进不了凭证也带不出主机名, 而参数会.

---

## 组 9 · 唯一装配点 (ADR-0004 §2)

**为什么存在**: 组 7 与组 8 互不相识, 在这里被串起来. **任何绕过这条链路的执行路径都是安全旁路.**

链路: 能力门 -> describe -> prepare -> evaluate -> (DENY / ASK 阻塞审批 + **批准后全量重验** /
ALLOW) -> 恢复绑定 -> issue -> execute.

| 文件 | 说明 |
|---|---|
| `application/tool_request/coordinator.py` | 上面那条链路 |
| `application/tool_request/dispatcher.py` | `ToolDispatcher` ABC + 协调器实现 |
| `application/tool_request/observations.py` | `ToolObservation` 与 `ObservationKind` |
| `application/tool_request/catalog_predicates.py` | mode 能力门 (plan 档只留 `PLAN_ONLY` 与只读) |
| `application/tool_request/audit.py` | `ToolAuditSink` ABC (**写前事件**) |
| `application/tool_request/session_audit.py` | 落进 `events.jsonl` 的实现 |
| `application/tool_request/run_observer.py` | 运行观察端口, **与审计分离** (ADR-0016 §4.3) |

`observations.py` 里还有一件与人机边界直接相关的事: `ObservationKind.disposition` 把每种结论
归到 `CONTINUE` / `BLOCKED` / `HALT` 三档 (ADR-0013 §4.3). 判据是**谁说的不行**, 不是 mode ——
人类拒绝的是意图, 策略拒绝说的是这条路不通, 两者后续处理完全不同. `APPROVAL_UNAVAILABLE`
是从 `APPROVAL_REQUIRED` 里拆出来的: "人拒绝了"与"这个环境里没有人"混成一种, 非交互环境下
模型会对着一个永远不会有人应答的提示反复重试.

`EXECUTABLE_NOT_FOUND` 走的是 `policy_denied` observation, disposition 是 `BLOCKED` ——
"这条路不通, 换条路合理", 模型下一轮自己改, 计入拒绝预算但不停轮.

**注意**: 两条顺序不能换 ——
1. 先建恢复保障再签发授权. 反过来会出现"已拿到执行授权但还原不了"的窗口.
2. 写前审计落盘之后才报"开始执行". 反过来终端会先于审计声称已开跑.

---

## 组 10 · 运行事件流 (ADR-0016)

**为什么存在**: 把观察事件的范围从"循环生命周期"扩到**整个 turn**. 终端只订阅事件, 不读 Provider
私有响应, 不读 `ToolPlan`, 不读 AgentLoop 内存状态.

| 文件 | 动作 | 说明 |
|---|---|---|
| `domain/agent/run_events.py` | 新增 | 20 个事件类型 + **密封 payload 值对象** (不用 `dict[str, object]`) + 信封 |
| `application/agent_run/events.py` | 新增 | `AgentRunEventBus`. `publish(kind, ...)` 而非 `publish(event)` —— 结构上让发布者填不了 sequence |
| `application/agent_run/tool_observer.py` | 新增 | 把工具链路观察翻译成事件 |
| `application/agent_run/__init__.py` | 新增 | |
| `domain/agent/events.py` | **删除** | 旧 `LoopEvent` |
| `application/agent_loop/events.py` | **删除** | 旧 `LoopEventBus`. **不保留两套并行总线** |

`ModelUsagePayload` 比 ADR 原稿多两个字段 (`total_tokens` / `estimated`) 与一个 `total` 属性.

```python
@property
def total(self) -> int:
    return self.total_tokens or (self.input_tokens + self.output_tokens)
```

**供应商给了就用供应商的**: 各家对"总数"的口径不一样 (reasoning token 有的并进 output, 有的
单列), 自己把几项加起来会和账单对不上. 没给才退回 `input + output`.

**注意**: `sequence` 由总线分配这条是"时间线可测"的全部依据. 谁都能填的话这条性质就没人保证.

---

## 组 11 · Agent 循环与 turn 改造

| 文件 | 动作 | 说明 |
|---|---|---|
| `application/agent_loop/builtin_loop.py` | 修改 | 从"只会回答"改成真 ReAct 环; 发运行事件; **观察回填带工具名与参数**; **重复调用闸** |
| `application/agent_turn/agent_turn_service.py` | 修改 | 注入 `ToolDispatcher`, 分发 `ToolRequestAction`, 多步循环 |
| `application/agent_loop/__init__.py` / `hooks.py` | 修改 | 去掉旧总线导出, docstring 指向 `AgentRunEvent` |
| `domain/agent/actions.py` | 修改 | `ObservationSource` 改枚举; 删掉三个无人产出的动作类型 |
| `domain/agent/state.py` | 修改 | 删 `ModePolicy`; `tool_catalog` 改 `ToolCatalog`; 删未赋值的 `system_prompt` |
| `domain/agent/__init__.py` | 修改 | 导出跟随 |

循环里有**三道互不重叠的闸**, 同步时别当成重复代码合并掉:

| 闸 | 拦什么 | 为什么单独存在 |
|---|---|---|
| `_MAX_IDENTICAL_CALLS = 2` | 同一工具 + 同一参数 | 拦"原地打转". 相同参数调上百次结果一样 |
| `_MAX_BLOCKED_CALLS = 3` | 本轮累计策略拒绝 | 拦"换着花样撞墙". `rm -rf build/` 换成 `find build -delete` 签名不同, 上一道闸放行 |
| `_tools_closed` | 人类拒绝 / 无人可裁决 | 立即收目录. 一次就够, 不给预算 |

**注意**:
- 重复闸 (`_MAX_IDENTICAL_CALLS = 2`) 与总预算是两回事: 总预算拦"活干得多", 它拦"同一件事重复做".
  相同参数调上百次, 每次结果一样, 总预算再大只是多转几百圈.
- 拒绝计数数**累计**不数连续: 连续计数会被一次成功的读取重置, 模型只要在两次被拒之间插一个
  无害调用, 计数器就永远到不了上限.
- `_close_tools` 必须调 `_abandon_pending`: 协议要求 assistant 消息里每个 `tool_call` 都有配对
  的 tool result. 直接丢掉排队项, 下一次请求就是残缺的, 供应商会拒.
- `_tools_closed` 这个标志**不是冗余的**. 只把 `_tools` 置空等于靠"没给你看你就不会要"——
  模型幻觉一个工具名, 或供应商重放上一条 `tool_call`, `_advance` 照样会派发. 强制点在派发那里.
- `_DEFAULT_MAX_MODEL_CALLS` / `_DEFAULT_MAX_TOOL_CALLS` 目前是 9999. 长任务确实可能几百次调用,
  按次数设限是错的约束.
- `ObservationSource` 必须含 `SECURITY` —— `tool_request/observations.py` 用到它.
- 每次模型调用都发 `MODEL_COMPLETED` (含流式中断与取消路径). 组 13 的终端渲染靠它收正文块 ——
  漏发会让半行正文一直挂在活动区.

---

## 组 12 · LLM 网关修正

**为什么存在**: 两处**执行正确性缺陷**, 不是展示问题.

| 文件 | 动作 | 说明 |
|---|---|---|
| `domain/model/streaming.py` | 修改 | `tool_call_delta` 单值改 `tool_call_deltas[]`. 只保留首项会让**并行工具调用整体消失** |
| `infrastructure/llm/adapters/openai_compatible.py` | 修改 | 解析全部 `delta.tool_calls`; `index` 缺省退回数组下标 (恒为 0 会让多个调用互相覆盖) |
| `application/llm/gateway/streaming.py` | 修改 | 累积器遍历列表 |
| `application/llm/gateway/default_gateway.py` | 修改 | 透传列表 |
| `application/llm/gateway/__init__.py` | 修改 | 导出跟随 |
| `domain/model/request.py` | 修改 | `CancelToken` 上移到 `shared` |

---

## 组 13 · CLI 接线, 斜杠命令与终端展示

| 文件 | 动作 | 说明 |
|---|---|---|
| `interfaces/cli/tool_wiring.py` | 新增 | **三层组合根**. 装配顺序本身就是安全约束的一部分 |
| `interfaces/cli/run_renderer.py` | 新增 | `TerminalRunRenderer`. turn 内**唯一**终端写入者 |
| `interfaces/cli/tty_approval.py` | 新增 | HITL 确认界面 (ADR-0016 §8.4) |
| `interfaces/cli/tty/select.py` | 新增 | 单层单选 (↑↓ + Enter + 数字快捷键). 审批用, **不导航不改状态** |
| `interfaces/cli/session_exit.py` | 新增 | `SessionExit` 异常. 由 `prompt_loop.QuitSignal` 移出并改名 |
| `interfaces/cli/commands/exit_command.py` | 新增 | `/exit` |
| `interfaces/cli/commands/tools_command.py` | 新增 | `/tools` —— 按当前 mode 走**与模型相同的目录谓词** |
| `interfaces/cli/commands/rules_command.py` | 新增 | `/rules` / `--revoke <id>` / `--prune` |
| `interfaces/cli/commands/recovery_command.py` | 新增 | `/undo` / `/checkpoints` / `/restore` / `/recovery` |
| `interfaces/cli/commands/mode_command.py` | 修改 | 加 `ModeSelectCommand` (`/mode` 面板) |
| `interfaces/cli/stream_render.py` | **删除** | 被 `run_renderer` 取代 |
| `interfaces/cli/prompt_loop.py` | 修改 | `QuitSignal` 移出到 `session_exit.py` |
| `interfaces/cli/bootstrap.py` | 修改 | 装配 `run_bus` + renderer + 工具栈 |
| `interfaces/cli/repl.py` | 修改 | `stream_view` 类型改 `TerminalRunRenderer`; 分派 `ManualShellIntent`; **`SessionExit` 捕获范围修正** |
| `interfaces/cli/wiring.py` | 修改 | 注册上面这批命令; 工具类命令按 `ToolStack` 是否装配决定注册与否 |
| `interfaces/cli/commands/add_dir_command.py` | 修改 | 重写为命令式 `/add-dir <path> [--write]` |
| `interfaces/cli/menus/config_menu.py` | 修改 | **见下面的"待确认的分歧"** |

### `repl.py` 的 `SessionExit` 捕获范围

`SessionExit` 现在有**两个抛出点** —— 输入框 (空行连按两次 Ctrl-C) 和 `/exit` 命令. 原来的
`try` 只包了 `prompt.read()`, `/exit` 抛出来的那个会一路穿出去变成 traceback:

```python
while True:
    try:
        # 读取与分派共用一个 except: SessionExit 有两个抛出点
        self._process_line(prompt.read())
    except SessionExit:
        break
    except EOFError:
        break
```

### `run_renderer.py`: 正文按 request_id 分块

一轮里模型可能被调用多次 (说一句 -> 调工具 -> 再说一句). 两条性质:

1. **每次调用的正文是独立一块**, 各自带 `●`, 不与另一次调用的正文拼成一段.
2. **一块正文在它后面那条过程行之前提交完**. 否则模型调工具前说的话会落到工具结果下面,
   读起来像是它执行完才说的.

收块有两个触发点: `MODEL_COMPLETED`, 以及 `_process()` —— 唯一的过程行出口. 把规则放在
`_process()` 上而不是散在每个事件处理里, 是因为后者只要漏掉一个事件那条时间线就会错位, 而
这种错位在只断言"包含某段文字"的测试里根本看不出来 (输出都在, 只是顺序反了).

`MODEL_COMPLETED` 是唯一一个**不打印任何行却必须收块**的事件, 所以只有它需要单独一个 handler.

### `run_renderer.py`: 每次模型调用后的用量行

报**本次调用**加**本轮累计**两个数. 只给单次的话, 一轮里调了七次模型的长任务得用户自己心算;
只给累计的话, 又看不出是哪一步吃掉的. 第一次调用不重复打累计 (那时两个数必然相等).
本地估算值标 `(估算)` —— 不标出来用户会拿它去核供应商账单.

安全分类器走的是网关的另一条路径, 不发运行事件, 因此**不进这个数** (ADR-0013 §11).

**注意**:
- `tool_wiring.py` 的装配顺序 —— 受保护路径先于执行画像 (画像绑定 roots_hash), 恢复层先于
  协调器, 分析器先于授权服务. 写乱了不会报错, 只会让某一层悄悄失效.
- `session_id` 必须用 `lambda` 延迟取: 组合根跑在 `session.start()` 之前.
- `/rules` 与 `/tools` **不在工具目录里**, LLM 请求不到. 能自己撤销规则的 Agent 也能自己创建规则.

---

## 组 14 · 人工 Shell 模式 (ADR-0017)

**整组同步.**

**为什么存在**: 用户偶尔需要一个不经 mode, 不经 HITL, 不经工具管线的真 Shell —— `clear` 清个屏,
`brew install` 装个东西. 走 `shell.run` 等于让 Agent 的安全模型去管人自己的操作, 那不是它的
职责; 而绕开 Forge 另开一个终端又会丢掉 cwd 与会话上下文.

| 文件 | 动作 | 说明 |
|---|---|---|
| `domain/manual_shell/request.py` | 新增 | `ManualShellRequest` (含 `command` 与 `interactive` 属性) + `TerminalSize` |
| `domain/manual_shell/result.py` | 新增 | `ManualShellResult`. **没有任何字段能装下命令或输出** |
| `domain/intents.py` | 修改 | 新增 `InputOrigin` 枚举与 `ManualShellIntent` |
| `application/intent_router.py` | 修改 | `route(raw_text, *, origin=PROGRAM)`; `ONE_SHOT_PREFIX = "# "` |
| `application/manual_shell/provider.py` | 新增 | Shell 会话抽象 |
| `application/manual_shell/service.py` | 新增 | `ManualShellService`. `observer` 是**必填位置参数** |
| `application/manual_shell/mutation_barrier.py` | 新增 | `ManualMutationBarrier` |
| `infrastructure/manual_shell/shell_selection.py` | 新增 | `SystemShellResolver`. 注入 `FORGE_SHELL=1` 与 `FORGE_SHELL_CWD` |
| `infrastructure/manual_shell/posix_pty.py` | 新增 | 前台进程组交接 |
| `infrastructure/manual_shell/windows_console.py` | 新增 | Windows 路径 |
| `interfaces/cli/shell_mode.py` | 新增 | REPL 侧入口 |
| `interfaces/cli/terminal_lease.py` | 新增 | 终端租约: 交出前先收活动区 |

### 两个入口

```
#            开一次完整交互式会话
# clear      跑一条就回来
```

一条 `clear` 要先进一次完整会话再退出来太重了, 所以一次性形态单独存在. 两种形态共用一个
`ManualShellIntent`, 因为它们的**信任语义完全相同**.

前缀字符只在 `ONE_SHOT_PREFIX` 一处定义, 改字符改那一行即可.

### `#` 的特权来自输入来源, 不来自这个字符

`InputOrigin` 默认是 `PROGRAM`, 只有 CLI 的前台 TTY 输入适配器传 `TTY_USER`.
`ManualShellIntent.__post_init__` 直接拒掉非 `TTY_USER` 的构造 —— 于是"让模型说一句 `#` 就拿到
不受裁决的 Shell"这条路**在类型层面就不存在**. 模型文本, 工具输出, 事件重放, resume 与任何
自动化入口出现的 `#` 一律走自然语言.

默认值选 `PROGRAM` 而不是 `TTY_USER`: 新增一个调用方时, 忘记传参的后果是少一项特权, 不是多一项.

**注意**:
- `ManualShellService` 的 `observer` 是**必填位置参数**, 别改成 `observer=None`. 进入人工 Shell
  时那条提示横幅是 ADR 要求的告知, 挂在可选参数上等于"忘了传就没有告知".
- 交互式会话用**前台进程组交接** (`process_group=0` + `tcsetpgrp` + 抑制 SIGTTOU), 不是 PTY.
  用 PTY 意味着 Forge 站在用户和自己的 shell 中间, 而这个模式的全部意义就是它不站在中间.
- 一次性命令成功时**不打印任何东西**. 127 / 9009 (命令找不到) 时提示"去掉开头的 `# `" ——
  那多半是用户想跟模型说话.
- `FORGE_SHELL=1` 与 `FORGE_SHELL_CWD` 让用户在自己的 prompt 里看得出"我现在在 Forge 开的
  shell 里". 没有这个标记, 用户会不知道该 `exit` 还是关窗口.
- `scripts/check_arch.py` 有 6 条 `application.manual_shell` 的互斥规则 (见组 16):
  人工 Shell 不能 import 工具, 安全, 恢复与审批任何一侧.

---

## 组 15 · 既有缺陷修复 (与上面几组无关, 可独立搬)

| 文件 | 动作 | 说明 |
|---|---|---|
| `domain/workspace/project.py` | 修改 | `ProjectConfig.__post_init__` 归一 `workspace_roots`. 修 `ValueError: workspace_roots 不能为空` |
| `infrastructure/project/toml_store.py` | 修改 | 缺 `primary_workspace_root` 时返回 None (当作没有配置) |
| `infrastructure/config/paths.py` | 修改 | 新增 `state_dir()` / `artifacts_dir()` / `recovery_dir()` |
| `domain/session/events.py` | 修改 | 新增工具与安全审计事件类型 |
| `application/session/session_service.py` | 修改 | `record_tool_event` |
| `src/forgecli/domain/policy/` | **删除** | 主仓里是删空后留下的空目录 |

---

## 组 16 · 测试与架构检查

| 路径 | 说明 |
|---|---|
| `scripts/check_arch.py` | 层间方向 + domain/application 不碰 Rich/prompt_toolkit/httpx + **工具与安全互不 import** + **人工 Shell 与三者互不 import** |
| `tests/support/fakes.py` / `loop_harness.py` | 共享 `PROFILE` 与循环夹具 |
| `tests/agent_run/` (8) | 事件总线契约, 循环时间线, HITL 界面, 终端渲染, **正文分块**, **用量展示**, 单选控件, 拒绝处理 |
| `tests/commands/` (5) | `/exit` `/mode` `/rules` `/tools` 与恢复类命令 |
| `tests/manual_shell/` (7) | 信任边界, 一次性命令, 屏障, 服务, 终端与隐私, **两个 `pty.fork()` 端到端** |
| `tests/security/` (3) | 受保护路径, 学习规则, **可执行文件找不到** |
| `tests/llm/` (1) | 并行 tool call delta 回归 |
| `tests/tool_request/` (2) | 审计 turn_id 绑定, 声明绑定 |
| `tests/tools/` (3) | `fs.write_patch` 精确替换, `fs.delete` 目录, `search.text`, `fs.list_files` |

**注意**: 主仓当前**没有 tests 目录**. ADR-0004/0013/0015 那一批约 294 个测试在副本里已丢失且
未提交过 git, 不可恢复 —— 现有 379 个测试覆盖 ADR-0016 / 0017, 本轮工具改造与几处专项修复,
不覆盖阶段 1 到 7 的全部实现.

---

## 待确认的分歧: `menus/config_menu.py`

副本比主仓**少了三个配置项**: 启用使用统计, 输出主题, 日志级别.

两边的 `domain/config/config_keys.py` 与 `effective_config.py` 都还完整定义着这三个 key ——
只有菜单里没有了. 我判断不出这是副本里的有意删除还是主仓后来补上的, 所以**没有把它写进同步
动作**. 直接照搬副本会静默删掉主仓这三个菜单项.

建议: 同步 `config_menu.py` 时保留主仓版本, 或者你确认一下这三项是不是有意去掉的.

---

## 同步后验证

```bash
cd <主仓> && make ci
```

= `poetry check` + `ruff check` + `ruff format --check` + `mypy --strict` + `arch` + `pytest`.

按组顺序搬时, 每搬完一组可以先跑 `poetry run mypy` 看依赖有没有断; 全部搬完再跑 `make ci`.

`make arch` 通过 = 分层没破. 它拦四类违规: 层间反向依赖, domain/application 碰终端框架,
工具与安全模块互相 import, 以及人工 Shell 碰这三者.

副本里 `.venv` 装的是**非可编辑安装**, `poetry run forge` 会跑旧代码. 想手验先跑一次
`poetry install`.

---

## 已在主仓完成, 无需同步

- `docs/adr/2026-08-03-0016-*.md` —— 直接在主仓编辑, 已是最新.
- `docs/adr/2026-07-29-0013-*.md` §4.3 与验收标准 —— 新增"拒绝之后循环怎么走"一节,
  与组 9 / 组 11 的实现对应.
- `docs/adr/2026-06-17-0004-*.md` §9 / §14 / 验收标准 —— 删除 `side_effect_class`,
  §9 改写为"崩溃后的结果未知", 明确 resume 不重放.
- `domain/tool/tool_call.py` 的 `ToolSpec` -> `ToolSchema` 改名及其 5 个消费点.

## 待搬的文档

- `docs/adr/2026-08-04-0017-采用井号入口提供人工Shell模式.md` —— 副本里那份是最新的
  (含一次性命令, `FORGE_SHELL` 标记与前缀字符的最终选择), 主仓那份还是旧稿. 一次文件覆盖.

## 已知与代码不一致的文档 (待修)

- **ADR-0004 §14 工具表**: 副作用类别列已随 `side_effect_class` 一起删除, 但**行还是旧的** ——
  `fs.write_patch` / `fs.delete` / `search.text` 都升了版本改了 schema; 缺 `fs.move`;
  `artifact.write` 那一行声明 `WORKSPACE_WRITE` 与"产物不落工作区"的设计冲突, 该工具建议直接去掉.
- **ADR-0013 §5**: 需要补 `EXECUTABLE_NOT_FOUND` 这一档与内建命令的处理 (组 8).
- **ADR-0013 §5.1**: `ApprovalScope` 已收窄到 `ONCE` / `WORKSPACE` 两档, 文档还写着四档
  (`session` / `always` 没有任何产出方). 但 `/rules` 列表 UX 是按 `always` 写的, 两处口径要一起定.
- **ADR-0014**: 状态仍是 Proposed, 需注明沙箱层缓期, 非沙箱条款已落地.
- **ADR-0015 / ADR-0017**: 状态仍是 Proposed, 可转 Accepted.

## 代码里已知未实现的

- **plan 档的计划评审** (补充 / 拒绝 / 同意 / 同意并执行) 从未实现.
- **平台事实还没进系统提示词**. 模型不知道自己在 Windows 还是 macOS, 所以仍会先发一次 `ls -al`;
  组 8 的改动只是把这次误发的代价从"打断人"降到"模型多花一轮". 真正的修法是把
  `EnvironmentFacts` 渲染进 `ModelRequest.system_prompt`, 尚未开始.
- **root 启动校验被注释掉了** (`interfaces/cli/bootstrap.py:181-183`), 是本地的有意决定,
  不是遗漏. 同步时保持注释状态, 别顺手打开.

---
## 附录 · 完整文件清单 (核对用)

机器生成, 与上面的分组表一一对应. 搬完一项打一个勾.

### 新增源文件 (121)

```
src/forgecli/application/agent_run/__init__.py
src/forgecli/application/agent_run/events.py
src/forgecli/application/agent_run/tool_observer.py
src/forgecli/application/manual_shell/__init__.py
src/forgecli/application/manual_shell/mutation_barrier.py
src/forgecli/application/manual_shell/provider.py
src/forgecli/application/manual_shell/service.py
src/forgecli/application/recovery/__init__.py
src/forgecli/application/recovery/coordinator.py
src/forgecli/application/recovery/recovery_service.py
src/forgecli/application/recovery/recovery_store.py
src/forgecli/application/security/__init__.py
src/forgecli/application/security/analyzers/__init__.py
src/forgecli/application/security/analyzers/network_analyzer.py
src/forgecli/application/security/analyzers/registry.py
src/forgecli/application/security/analyzers/script_analyzer.py
src/forgecli/application/security/analyzers/shell_analyzer.py
src/forgecli/application/security/analyzers/unknown_analyzer.py
src/forgecli/application/security/analyzers/workspace_analyzer.py
src/forgecli/application/security/approval_service.py
src/forgecli/application/security/authorization_service.py
src/forgecli/application/security/classifier.py
src/forgecli/application/security/executable_resolver.py
src/forgecli/application/security/gateway_classifier.py
src/forgecli/application/security/learned_rules.py
src/forgecli/application/security/policy_engine.py
src/forgecli/application/security/risk_cache.py
src/forgecli/application/security/wiring.py
src/forgecli/application/security/workspace_grants.py
src/forgecli/application/tool_request/__init__.py
src/forgecli/application/tool_request/audit.py
src/forgecli/application/tool_request/catalog_predicates.py
src/forgecli/application/tool_request/coordinator.py
src/forgecli/application/tool_request/dispatcher.py
src/forgecli/application/tool_request/observations.py
src/forgecli/application/tool_request/run_observer.py
src/forgecli/application/tool_request/session_audit.py
src/forgecli/application/tools/__init__.py
src/forgecli/application/tools/artifact_store.py
src/forgecli/application/tools/builtin/__init__.py
src/forgecli/application/tools/builtin/base.py
src/forgecli/application/tools/builtin/fs_list_files.py
src/forgecli/application/tools/builtin/fs_move.py
src/forgecli/application/tools/builtin/fs_read_file.py
src/forgecli/application/tools/builtin/fs_write.py
src/forgecli/application/tools/builtin/git_read.py
src/forgecli/application/tools/builtin/plan_update.py
src/forgecli/application/tools/builtin/search_text.py
src/forgecli/application/tools/builtin/shell_run.py
src/forgecli/application/tools/command_executor.py
src/forgecli/application/tools/registry.py
src/forgecli/application/tools/resource_governor.py
src/forgecli/application/tools/runtime.py
src/forgecli/application/tools/tool.py
src/forgecli/application/workspace/__init__.py
src/forgecli/application/workspace/execution_context.py
src/forgecli/application/workspace/filesystem_view.py
src/forgecli/domain/agent/run_events.py
src/forgecli/domain/execution/__init__.py
src/forgecli/domain/execution/environment.py
src/forgecli/domain/execution/profile.py
src/forgecli/domain/manual_shell/__init__.py
src/forgecli/domain/manual_shell/request.py
src/forgecli/domain/manual_shell/result.py
src/forgecli/domain/recovery/__init__.py
src/forgecli/domain/recovery/checkpoint.py
src/forgecli/domain/recovery/mutation.py
src/forgecli/domain/security/__init__.py
src/forgecli/domain/security/approval.py
src/forgecli/domain/security/context.py
src/forgecli/domain/security/decision.py
src/forgecli/domain/security/executable_identity.py
src/forgecli/domain/security/hard_deny.py
src/forgecli/domain/security/modes.py
src/forgecli/domain/security/protected_paths.py
src/forgecli/domain/security/risk.py
src/forgecli/domain/security/rules.py
src/forgecli/domain/security/script_facts.py
src/forgecli/domain/security/script_patterns.py
src/forgecli/domain/security/shell/__init__.py
src/forgecli/domain/security/shell/builtins.py
src/forgecli/domain/security/shell/cmd.py
src/forgecli/domain/security/shell/command_plan.py
src/forgecli/domain/security/shell/expansion.py
src/forgecli/domain/security/shell/parser.py
src/forgecli/domain/security/shell/posix.py
src/forgecli/domain/security/shell/powershell.py
src/forgecli/domain/security/shell/tokens.py
src/forgecli/domain/security/shell/wrappers.py
src/forgecli/domain/security/vocabulary.py
src/forgecli/domain/tool/authorization.py
src/forgecli/domain/tool/catalog.py
src/forgecli/domain/tool/errors.py
src/forgecli/domain/tool/result.py
src/forgecli/infrastructure/execution/__init__.py
src/forgecli/infrastructure/execution/environment_probe.py
src/forgecli/infrastructure/execution/local_command_executor.py
src/forgecli/infrastructure/manual_shell/__init__.py
src/forgecli/infrastructure/manual_shell/posix_pty.py
src/forgecli/infrastructure/manual_shell/shell_selection.py
src/forgecli/infrastructure/manual_shell/windows_console.py
src/forgecli/infrastructure/recovery/__init__.py
src/forgecli/infrastructure/recovery/fs_recovery_store.py
src/forgecli/infrastructure/security/__init__.py
src/forgecli/infrastructure/security/protected_paths_builder.py
src/forgecli/infrastructure/security/toml_learned_rules_store.py
src/forgecli/infrastructure/tools/__init__.py
src/forgecli/infrastructure/tools/fs_artifact_store.py
src/forgecli/infrastructure/workspace/__init__.py
src/forgecli/infrastructure/workspace/os_filesystem_view.py
src/forgecli/interfaces/cli/commands/exit_command.py
src/forgecli/interfaces/cli/commands/recovery_command.py
src/forgecli/interfaces/cli/commands/rules_command.py
src/forgecli/interfaces/cli/commands/tools_command.py
src/forgecli/interfaces/cli/run_renderer.py
src/forgecli/interfaces/cli/session_exit.py
src/forgecli/interfaces/cli/shell_mode.py
src/forgecli/interfaces/cli/terminal_lease.py
src/forgecli/interfaces/cli/tool_wiring.py
src/forgecli/interfaces/cli/tty/select.py
src/forgecli/interfaces/cli/tty_approval.py
```

### 修改的既有文件 (34)

```
src/forgecli/application/agent_loop/__init__.py
src/forgecli/application/agent_loop/builtin_loop.py
src/forgecli/application/agent_loop/hooks.py
src/forgecli/application/agent_turn/agent_turn_service.py
src/forgecli/application/intent_router.py
src/forgecli/application/llm/gateway/__init__.py
src/forgecli/application/llm/gateway/default_gateway.py
src/forgecli/application/llm/gateway/streaming.py
src/forgecli/application/session/session_service.py
src/forgecli/domain/agent/__init__.py
src/forgecli/domain/agent/actions.py
src/forgecli/domain/agent/state.py
src/forgecli/domain/intents.py
src/forgecli/domain/model/request.py
src/forgecli/domain/model/streaming.py
src/forgecli/domain/session/events.py
src/forgecli/domain/tool/__init__.py
src/forgecli/domain/tool/capability.py
src/forgecli/domain/tool/hashing.py
src/forgecli/domain/tool/plan.py
src/forgecli/domain/tool/spec.py
src/forgecli/domain/workspace/project.py
src/forgecli/infrastructure/config/paths.py
src/forgecli/infrastructure/llm/adapters/openai_compatible.py
src/forgecli/infrastructure/project/toml_store.py
src/forgecli/interfaces/cli/bootstrap.py
src/forgecli/interfaces/cli/commands/add_dir_command.py
src/forgecli/interfaces/cli/commands/mode_command.py
src/forgecli/interfaces/cli/menus/config_menu.py       ← 见"待确认的分歧"
src/forgecli/interfaces/cli/prompt_loop.py
src/forgecli/interfaces/cli/repl.py
src/forgecli/interfaces/cli/wiring.py
src/forgecli/shared/cancellation.py
src/forgecli/shared/json_schema.py
```

### 从主仓删除 (3 个文件 + 1 个空目录)

```
src/forgecli/application/agent_loop/events.py
src/forgecli/domain/agent/events.py
src/forgecli/interfaces/cli/stream_render.py
src/forgecli/domain/policy/            (空目录)
```

### 非源码

```
scripts/check_arch.py                            新增
Makefile                                         修改 (加 arch 目标)
pyproject.toml                                   修改 (pythonpath 加 tests)
tests/support/fakes.py                           新增
tests/support/loop_harness.py                    新增
tests/agent_run/test_denial_handling.py          新增
tests/agent_run/test_event_bus.py                新增
tests/agent_run/test_hitl_view.py                新增
tests/agent_run/test_loop_timeline.py            新增
tests/agent_run/test_output_blocks.py            新增
tests/agent_run/test_run_renderer.py             新增
tests/agent_run/test_select_one.py               新增
tests/agent_run/test_usage_display.py            新增
tests/commands/test_exit_command.py              新增
tests/commands/test_mode_command.py              新增
tests/commands/test_recovery_commands.py         新增
tests/commands/test_rules_command.py             新增
tests/commands/test_tools_command.py             新增
tests/llm/test_parallel_tool_call_deltas.py      新增
tests/manual_shell/test_barrier.py               新增
tests/manual_shell/test_one_shot.py              新增
tests/manual_shell/test_posix_pty_e2e.py         新增
tests/manual_shell/test_repl_entry_e2e.py        新增
tests/manual_shell/test_service.py               新增
tests/manual_shell/test_terminal_and_privacy.py  新增
tests/manual_shell/test_trust_boundary.py        新增
tests/security/test_executable_not_found.py      新增
tests/security/test_learned_rules.py             新增
tests/security/test_protected_paths.py           新增
tests/tool_request/test_audit_turn_binding.py    新增
tests/tool_request/test_declaration_bound.py     新增
tests/tools/test_fs_list_files.py                新增
tests/tools/test_fs_write_and_delete.py          新增
tests/tools/test_search_text.py                  新增
```
