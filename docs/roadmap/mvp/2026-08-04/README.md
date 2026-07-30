# 2026-08-04：沙箱能力探测、实例生命周期与 /add-dir

## 今日目标

按 ADR-0014 完成 Forge 启动时的沙箱能力探测、Shell 执行时的临时沙箱实例，以及命令式目录授权。

## 今日范围

- 实现 `SandboxManager` 和 `ExecutionEnvironmentProfile`。
- 能区分 `STRONG_SANDBOX`、`PARTIAL_SANDBOX` 和 `NO_SANDBOX`。
- 接入 macOS Seatbelt、Linux bubblewrap 和 WSL2 Linux 执行环境的 Provider 边界；无法使用时进入
  NoSandboxProvider。
- 每次 Shell 执行按当前 `SandboxPolicy` 创建实例并在完成后销毁。
- 脚本优先使用只读工作区 + 临时可写层或等效策略。
- 实现 `/add-dir <path>`、`/add-dir <path> --write`、`--list` 和 `--remove`。
- `/add-dir` 默认只读，只能由用户显式发起；策略变化递增 `policy_version`。
- 新目录只对后续创建的实例生效，旧实例不动态扩大权限。
- 持久 Shell 策略变化时保存并恢复允许的 cwd 和环境变量。

## 非目标

- 不实现完整虚拟机隔离。
- 不允许沙箱内进程自行扩大宿主机目录访问。
- 不在当前实例中动态注入新的宿主路径。

## 最终产物

- 沙箱能力探测和自测 fake。
- SandboxProvider / NoSandboxProvider 接口。
- SandboxPolicy、policy version 和实例生命周期测试。
- `/add-dir` 解析、路径保护、读写授权和撤销测试。

## 验收标准

- Forge 启动时生成执行环境档案。
- ShellTool 通过当前 Provider 创建执行实例。
- 新增目录只在下一次执行中可见。
- 写权限需要显式确认。
- 旧实例和运行中命令不会自动获得新目录权限。
- WSL2 执行环境使用 Linux Provider，并有 Windows 主机路径挂载边界测试。
- 持久 Shell 会话在策略变更后能恢复允许的 cwd 和环境变量。
- 沙箱创建失败时安全降级，不假设沙箱仍然有效。
- 运行沙箱单测并执行 `make ci`。

## 关联决策

- ADR-0014 §1–§10。
