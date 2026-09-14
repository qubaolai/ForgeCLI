# ADR-0052: Full Access 改成启动期声明的档位, 并增加 Manual 档

## 状态

Proposed

本 ADR 改的是**进入模式的门**与**模式的清单**, 不改任何一档的裁决语义: Hard Deny,
Mandatory Ask, 围栏, UNCONFINED 回退, 学习规则与计划评审升档全部不变.

## 日期

2026-09-14

## 背景

### 对照: Claude Code 的五个模式不在同一层

Claude Code 的 Manual / Accept edits / Plan 是常驻档, Auto 是"条件可用"档, Bypass
permissions 是"启动期声明"档. 差别不在裁决规则, 而在**能不能进入**. Bypass 要过四道门:

| 门 | 机制 |
| --- | --- |
| 启动期声明 | 只能在启动时带 `--permission-mode bypassPermissions` / `--dangerously-skip-permissions` (直接进), 或 `--allow-dangerously-skip-permissions` (只加进 Shift+Tab 循环, 不激活), 或用户级 / 托管级 `permissions.defaultMode`. "You can't enter bypassPermissions from a session you started without it enabled." |
| 一次性责任确认 | 第一次交互式启动弹警告, 接受后存到用户级设置; 拒绝就退出. 非交互不弹, `--bg` 在未接受前直接拒绝. |
| 环境拒绝 | root / sudo 拒绝启动, 在已识别的沙箱里跳过这条检查. |
| 管理员禁用 | `permissions.disableBypassPermissionsMode: "disable"`, 任何 settings 文件都认. |

另外两条边界: **项目级设置不能开它** (只认 user / managed, 不认仓库里的
`.claude/settings.json`); 每个界面自己有开关 (Desktop 的 "Allow bypass permissions mode",
VS Code 的 "Allow dangerously skip permissions"), 云会话根本没有这一档.

### 现状: ForgeCLI 的 full_access 裁决更严, 进入更松

`full_access` (`SandboxLevel.FULL_ACCESS` x `ApprovalPolicy.NEVER`) 的裁决语义已经比
Claude Code 的 Bypass 严: `policy_engine._only_hard_deny` 要求 `fence.unrestricted and
confined` 同时成立, 围栏没立起来时退回逐项确认; Hard Deny (`wrappers.py` 19 个入口命令,
`protected_paths.py`, 凭证外泄) 所有模式一律拦; 破坏性写入前工作区快照照常落盘.

进入侧什么门都没有:

- `session_app._step_mode` 沿 `MODE_PRESETS` 四档全走, 从 plan 按三下 Tab 就到 full_access.
- `/mode full_access` 与 `POST /api/v1/mode {"mode": "full_access"}` 直接调
  `ProjectRuntime.set_mode`, 无任何检查.
- 没有启动参数, 没有一次性确认, 没有禁用开关. ADR-0009 决策 2 的 root 拒绝随整个 ADR
  一起 Discarded.
- 唯一一道防线是 `SessionService.set_mode` 的"模式不落盘, 每个会话从 accept_edits
  起步". 它防的是**静默继承**, 不防**顺手按到**.

### 缺一档: 每一次读写都要人看

`ApprovalPolicy.ALWAYS` 的语义是"Shell 命令都要你点头", 文件工具的读写不问. 没有一档能
表达"每一次读写都过人": 想逐步看模型在做什么的人只能选 plan, 而 plan 根本改不了文件.
Claude Code 的 Manual 档读文件不问, 改文件问; 这里要的比它更严, 读也问.

### 两个界面在同一时间各自漂移

终端 `/mode` 是"整档预设 / 隔离 / 审批"三块的两级菜单, Web 的模式菜单是"隔离 / 审批"
两块, 预设名只出现在按钮上没有入口. 同一个问题在两处得到不同答案: 预设里选了 Auto,
审批那一块看到的是轴上的另一个名字. 而 Web 的预设列表是前端自己写的一份
(`stance.ts` 的 `modePresets`), 与后端 `PRESET_NAMES` 是第二份定义.

## 决策

### 1. Full Access 出预设列表, 除非启动时带 `--allow-full-access`

`domain/intents.available_presets(full_access_allowed=...)` 是唯一的清单来源. 参数是
启动时定下的事实, 整个进程不变, 经 `ProjectRuntimeRegistry` -> `ProjectRuntime` 传到
三条入口: Tab 循环 (`session_app._step_mode`), `/mode` (`commands/mode.py`), Web
(`GET /api/v1/modes`). 没放开时 Full Access **不出现**, 而不是出现了再拒绝.

门只设一处: `ProjectRuntime.set_mode` 对 `mode.unrestricted and not
full_access_allowed` 抛 `ModeUnavailableError`. 三条入口都经它, `/mode full_access`
这种绕过菜单过滤的参数形式也拦得住. Web 把它映成 403, 与"turn 运行中"的 409 分开.

判据是 `SessionMode.unrestricted` (审批档为 NEVER) 而不是预设名: 一个"围栏内不问"的
姿态无论叫什么名字都该过这道门.

两条启动路径都认这个参数. `interfaces/app.py` 只把它交下去, 门在
`interfaces/full_access_gate.resolve_full_access` 里, 由 `tui/bootstrap.run` 与
`web/server.run` 在装好日志之后各调一次.

对应 Claude Code 的 `--allow-dangerously-skip-permissions`: 只放进循环, 不直接进.
不提供"启动就在 full_access"的形式 —— 模式不落盘的那条不变量 (每个会话从
accept_edits 起步) 在这里同样成立, 进那一档永远是会话里的一次显式动作.

### 2. 一次性确认, 记在 Forge home, 不进 SCHEMA

第一次带 `--allow-full-access` 启动时, 在终端里打出这一档在 ForgeCLI 里的语义
(围栏拦的照拦, 变的只是围栏拦不住的那些还问不问), 问一次. 接受写
`~/.forge/full-access-consent.json` (`infrastructure/security/full_access_consent`),
以后不再问; 拒绝或 Ctrl-C 以 `ExitCode.FULL_ACCESS_REFUSED` (8) 退出.

没有 TTY 时拒绝并说清楚去哪里确认 (与 Claude Code 对 `--bg` 的处理一样). 裸 `forge`
起 Web 的那条命令行本身就是一个终端, 所以 Web 入口也在终端里问, 页面上只看得到
"有没有这一档".

它是**状态**不是配置偏好: 记的是"某时某刻有人确认过", 所以不进 `config_keys.SCHEMA`,
也就不能经 `/config` 写出来 —— 跳过对话框的唯一办法是真的在终端里回答一次. 落在 Forge
home 而不是项目目录: 确认的是这台机器上的这个人, 不是某个仓库.

### 3. `security.disable_full_access`: 应用级, 只能收紧

`ConfigKey` 一条, `ConfigLevel.APP`, BOOL, 默认 false. 开着时 `--allow-full-access`
被**拒绝启动** (同一个退出码 8), 而不是静默去掉参数: 用户带了参数却发现菜单里没有那一档,
会去翻文档而不是翻配置.

应用级而不是项目级, 与 Claude Code 的 `disableBypassPermissionsMode` 只认用户级 / 托管级
同一个理由: 一个 clone 下来的仓库不能替用户决定跳过审批, 反过来它也不该能替用户把这一档
锁死. 关掉它不等于放开, 放开仍要带参数.

在进程启动时读 (`interfaces/runtime/startup_config`, 从 `logging_wiring` 里拆出来的同一条
"直接读 config.json 原始键值"的路径): 拒绝要发生在任何项目锁拿到之前.

### 4. 新增 `ApprovalPolicy.MANUAL` 与预设 `manual`

`manual` = `SandboxLevel.WORKSPACE_WRITE` x `ApprovalPolicy.MANUAL`. 每一次读写都要点头:
`fs_read` / `search_text` 的读, `fs_apply_patch` 的写, 以及每一条 Shell 命令 —— 不管
Forge 看不看得清它. 只有不碰工作区文件的那几类调用 (写计划, 向人提问, 记忆, 读回自己
落盘的输出, 已裁决过的子进程启动) 仍然自动.

编译到围栏上是 `FencePolicy.automatic_workspace_io` 一个新字段 (manual 为 False, 其余
为 True), 由 `budget.fence_allowed_capabilities` 消费: 为假时 `WORKSPACE_READ` 退出
`_ALWAYS`, `_WORKSPACE_MUTATION` 不再加入. `automatic_shell` 在这一档同样为假 (下界仍来自
隔离档, manual 再把它关掉), `automatic_opaque_execution` 改成逐档点名 (AUTO, NEVER) 而不是
`is not ALWAYS` —— 后者在 manual 加进来之后会把最严的一档算成最松的.

围栏多了一个字段, 每一档的 `policy_hash` 都变了, 旧学习规则本来就对不上;
`POLICY_VERSION` 递增到 6 是把这件事写明.

`PolicyEngine` 在这一档下的 ASK 文案改成"这一档每一次读写都要你确认": 原来那句"围栏兜
不住这些能力"在这里是错的, 围栏兜得住, 是这一档本来就每次都问.

预设次序 manual -> plan -> accept_edits -> auto -> full_access. manual 排在 plan 前面而
不是后面, 虽然它的围栏 (工作区可写) 比 plan (只读) 宽: 这条梯度量的是"有多少事不问人就
做了", manual 一件都不做. plan 放第二是因为它在 ForgeCLI 里是"只出方案"的起点档, 计划
评审从它升到 auto (ADR-0038); 在 manual 档批准计划不改档, 选它的人要的正是每一步都看一眼.

### 5. 两个界面只列预设, 接口只认预设名

终端 `/mode` 收成一层菜单, Web 模式菜单只列预设; 隔离档与审批档两个轴仍是领域模型
(`fence_for` 按两个轴编译), 但不再单独露给人调. 四档预设已经覆盖了有用例的组合,
"强隔离 + 全自动"这类自由组合在菜单里只是多一层要解释的东西.

`POST /api/v1/mode` 只收 `{"mode": "<预设名>"}`; `{"sandbox": ...}` 这种按轴的请求不再收
—— 一个没有任何页面会发的请求形状, 留着只会让读路由的人以为前端还有那个入口.
`SessionMode.from_value` 仍认 `sandbox/approval` 整串, 那是落盘的学习规则在用.

Web 的预设列表来自 `GET /api/v1/modes` (名字, 说明, 每个预设对应的两个轴), 前端
`stance.ts` 不再写死一份. 从 registry 取而不是从当前 runtime: 放没放开是进程级事实,
页面在还没激活项目时就要画得出这个菜单.

### 6. 不照搬的两条

- Claude Code 在 bypass 可用时连 plan 模式的拦截也不执行. 这里的 plan 是 `READ_ONLY`
  围栏, 在系统调用那一刻拦, 与审批档无关, 保持不动.
- Claude Code 的 Auto 靠第二个模型做分类器. 这里的 `auto` 是"围栏内自动, 越界才问",
  机制不同, 不为了名字对齐去加分类器.

root 拒绝 (ADR-0009 决策 2) 本 ADR 不恢复: `full_access` 在 UNCONFINED 时退回逐项确认
这一条已经覆盖了"没有沙箱就不给 bypass"的场景, 而"以 root 跑但围栏立起来了"是否该拒绝
是另一个问题, 留待 `protected_paths_builder` 那条线处理.

## 备选方案

- **在切模式时弹确认, 而不是在启动时.** Web 端要多一个确认对话框与一个"未确认"状态的
  往返; 终端端要在输入框接管终端之后再插一个问句. 启动时问一次把两个入口收成一条路径,
  也与 Claude Code 的做法一致.
- **把确认记成 SCHEMA 里的 BOOL 配置.** 那样 `/config set` 就能跳过对话框. 确认的意义
  正是"有人在终端里回答过", 不该有第二条路.
- **manual 只问写不问读 (Claude Code 的 Manual).** 用户要求的是读写都问; 而且"读"在这里
  包括 `search_text` 全仓检索, 那是模型看见了什么的边界, 不只是改了什么.
- **保留按轴的 API 与菜单, 只把预设加进去.** 两套入口同时存在时, 同一个状态在两处有两个
  名字, 见背景末节.

## 影响

### 收益

- Full Access 从"按三下 Tab 就到"变成"启动时声明 + 一次确认 + 可锁死", 与裁决侧已有的
  严格程度对齐.
- 多一档 manual, 逐步看模型在做什么的人不必再在"只读"与"自动改"之间二选一.
- 模式的清单与文案只有 `MODE_PRESETS` 一份, 终端与 Web 照着画.

### 代价

- 旧学习规则一次性失效 (policy_hash 变了). 它们在执行画像变更之后本来也已经不再命中.
- manual 档下 `search_text` 每次都问, 会很吵. 这是这一档的定义, 不是缺陷; 嫌吵切
  accept_edits.
- `FencePolicy` 多一个字段, 缺省 False: 手写 `FencePolicy()` 的测试替身默认按最严的办.

### 已知边界

- 一次性确认是每台机器一次, 不是每个项目一次. 与 Claude Code 一致.
- `security.disable_full_access` 没有托管层, 现阶段只是"用户把自己锁在外面".
- `available_presets` 只过滤 `unrestricted` 一档. 将来若有第二个要启动期声明的姿态,
  判据要扩, 门不用动.

## 验收标准

- 不带 `--allow-full-access` 时: Tab 循环到 auto 停下; `/mode full_access` 报
  `ModeUnavailableError` 的文案; `GET /api/v1/modes` 不含 full_access;
  `POST /api/v1/mode {"mode": "full_access"}` 回 403.
- 带 `--allow-full-access`: 第一次在终端里问一次, 拒绝以退出码 8 结束且不写 consent 文件;
  接受后写文件, 第二次启动不再问, 且非 TTY 也能起.
- `security.disable_full_access=true` 时带参数启动以退出码 8 结束, 不问.
- manual 档: `fence_for` 编译出的围栏 `automatic_workspace_io` / `automatic_shell` /
  `automatic_opaque_execution` 全为假; `fence_allowed_capabilities` 不含 WORKSPACE_READ /
  WORKSPACE_WRITE / EXECUTE_SHELL, 含 PLAN_ONLY / USER_PROMPT.
- accept_edits / auto / plan / full_access 编译出的围栏与本 ADR 之前逐字段相同, 只多
  `automatic_workspace_io=True`.

## 关联文档

- ADR-0009 (Discarded) 决策 12 / 13: `full_access` 的定位, "配置只能收紧".
- ADR-0030 决策 4 (2026-08-28 修订): `full_access` 的裁决语义, UNCONFINED 回退.
- ADR-0038: 计划评审升档到 auto.
- ADR-0046: accept_edits 按触达事实裁决; `FencePolicy` 的两个自主性字段.
- Claude Code 文档 "Choose a permission mode" 的 bypassPermissions 一节.
