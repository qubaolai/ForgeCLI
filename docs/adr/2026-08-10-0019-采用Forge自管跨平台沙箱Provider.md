# ADR-0019：采用 Forge 自管跨平台沙箱 Provider

## 状态

Superseded by ADR-0030 (2026-08-21)

> **更正**: 原状态写的是"Accepted(2026-08-10 实现首个纵向切片)", 那个切片指的是
> 下面"Shell 规范化配套变化"一节, **不是**决策 1-5 的沙箱. 决策 1-5 从未实现 ----
> 全库检索 `seatbelt` / `sandbox-exec` / `bwrap` / `unshare` / `AppContainer`
> 无任何命中, `environment_probe.py` 恒定返回 `IsolationLevel.NO_SANDBOX`.
>
> 一份 ADR 同时承担"沙箱"与"Shell 规范化"两件事, 后果是其中一件静默没做而状态栏
> 看不出来, 另一件实现时把 `find -exec` 简写成了 `find` (2026-08-21 修复).
>
> 分流: 已落地的"Shell 规范化配套变化"归入 ADR-0013 继续有效;
> 未落地的决策 1-5 由 ADR-0030 重新设计. ADR-0030 明确废弃本文决策 4 的临时可写
> 工作区 (它会丢掉子进程生成的文件, 且与 ADR-0015 RecoveryStore 重复).

## 背景

Shell、测试框架和任意脚本不能只依赖字符串规则保证安全。Forge 需要在裁决通过后继续
限制进程可访问的文件、网络、进程与资源，同时必须跨 macOS、Linux、WSL2 和原生
Windows 给出真实能力画像。第三方统一沙箱运行时会引入额外供应链、安装和版本耦合，
因此本决策不采用 SRT。

Forge “自己实现”指 Forge 自己拥有 Provider 契约、可信策略生成、能力自测、实例生命
周期、执行画像绑定与失败语义；不表示在用户态重新实现 Seatbelt、mount namespace 或
Windows 内核隔离。

## 决策

### 1. 统一入口与生命周期

`ShellTool` 只调用 `CommandExecutor`。组合根注入 `SandboxManager`，Manager 在启动时
探测候选 Provider，并在每次执行时按冻结的 `SandboxPolicy` 创建一个
`SandboxInstance`：

```text
ShellTool
  → ToolRequestCoordinator / PolicyEngine
  → SandboxManager
  → SandboxProvider.create(policy, request)
  → SandboxInstance.run()
  → SandboxInstance.close()
```

Provider 创建或运行失败时返回结构化工具失败，不得在同一授权下回退到更弱 Provider。
没有平台隔离时也必须经过显式 `NoSandboxProvider`，不存在宿主直连旁路。

### 2. 平台 Provider

- macOS：`SeatbeltProvider` 生成 Forge 可信 profile，默认拒绝写入与网络，只开放系统
  运行时、授权只读目录和实例临时目录。
- Linux 与 WSL2：`BubblewrapProvider` 创建新的 mount、PID、session 和 network
  namespace；真实根只读，临时工作区副本覆盖挂载到原工作区绝对路径。
- 原生 Windows：当前使用 `NoSandboxProvider`。规则引擎按 `NO_SANDBOX` 收紧，不能把
  Windows 进程组或普通 ACL 冒充完整隔离。

以上是平台机制映射，不是最终固定命令清单。新 Provider 只要实现相同端口并通过行为
自测即可加入候选。

### 3. 能力必须行为自测

启动探测至少实际验证：临时目录可写、边界外写入失败、禁网策略无法连接宿主监听端口。
探测结果冻结为：

```text
sandbox_provider / provider_version
filesystem_isolation / network_isolation / process_isolation
resource_limits / overlay_or_temp_workspace
sandbox_policy_hash / profile_version
```

只有全部强能力都经验证时才是 `STRONG_SANDBOX`。当前实现中：

- Seatbelt 没有独立 PID namespace，且尚未证明 CPU/内存/进程数硬上限，最多为 Partial。
- bubblewrap 当前只读暴露宿主根，未隐藏全部可读文件，且资源硬上限尚未完整证明，最多
  为 Partial。
- 自测失败或 Provider 缺失为 No Sandbox。

执行画像和策略 hash 进入授权绑定；画像变化使旧授权失效。

### 4. 临时可写工作区

每个受隔离实例复制授权工作区到随机临时根。相对 cwd 和 bubblewrap 中的原绝对工作区
路径都指向临时副本；`TMPDIR/TMP/TEMP` 同样指向实例私有目录。实例关闭后只删除由
`mkdtemp` 返回的精确根。

临时层中的测试产物不会自动 materialize 到真实工作区。只有文件系统隔离自测通过、
临时层启用且没有外部读写授权时，恢复门才签发 `ephemeral-sandbox:<policy_hash>` 绑定。
`fs.write_file` 等直接写工具仍需要 ADR-0015 RecoveryStore。

### 5. 默认策略

- 网络关闭。
- 工作区源不直接写入，脚本写入临时副本。
- 外部目录为空。
- 可见环境使用既有 allowlist 与受控 PATH。
- 超时、输出上限和进程回收继续由 `LocalCommandExecutor` 执行。

网络或外部真实写入不是一次命令审批可以隐式扩大的能力。策略不具备请求能力时直接
拒绝并要求单独改变会话资源授权。

## Shell 规范化配套变化

同一切片扩展 ADR-0013：

- `--recursive`、`--force=value`、组合短参数和 Windows `/S` 进入规范参数语义；
- POSIX 简单 glob 在冻结 cwd/文件系统视图下最多展开 10,000 个目标，递归或超限 glob
  保持 UNKNOWN；
- POSIX、cmd 和 PowerShell 重定向进入 read/write effects，heredoc/here-string 不误作
  文件路径；
- `eval`、`xargs`、`find -exec`、`watch`、`parallel`、`Invoke-Expression`、
  `Start-Process` 等静态可见内层继续递归预检，外层仍保留动态事实；
- inline、heredoc 和可冻结工作区脚本进入确定性副作用分析，提取文件写删、网络、子
  进程、动态执行、所有权/权限变化和外部不可逆信号；正文 hash 进入有效计划。

这些表均为可替换语义基线。未匹配到信号不等于安全，解析不完整继续走 UNKNOWN/ASK。

## 当前限制

- 暂未提供完整 `/sandbox` 开关和 `/add-dir --write/--remove` 策略持久化 UI。
- 临时副本是完整复制，不是内核级 Copy-on-Write，超大仓库仍需 reflink/overlay 优化。
- bubblewrap 尚未构造最小只读根；因此不能隐藏全部宿主可读文件。
- 原生 Windows 尚无 Strong/Partial Provider。
- CPU、内存和进程数硬限制未全部落地，故当前不会产生 Strong 画像。

## 验收

- Provider 运行期失效不会回退到下一 Provider。
- 每次执行都创建并销毁新实例。
- 自测失败如实降级，Shell 仍经过 NoSandboxProvider 和规则引擎。
- `rm --recursive --force /`、`find -exec` 内层 root 删除和脚本 root 删除在执行前拒绝。
- glob、重定向和脚本副作用进入 `ToolPlan` 与 plan hash。
- 直接文件写入不会借临时沙箱恢复绑定绕过 RecoveryStore。

## 替代关系

本 ADR 替代 ADR-0014 的 SRT Provider、安装、分发和平台选型部分；ADR-0014 关于能力
自测、执行画像绑定、实例生命周期、受保护路径和不静默降级的约束继续有效。
