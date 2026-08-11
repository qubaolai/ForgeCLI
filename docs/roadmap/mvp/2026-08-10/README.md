# 2026-08-10：跨平台沙箱接入与 Shell 规范化语义扩展

## 目标

不采用 SRT，在既有工具授权链之后接入 Forge 自管 Provider，确保任何 Shell 都经过
`SandboxManager → SandboxInstance`；同步封闭长参数、glob、重定向、间接执行和脚本
副作用的明显分析缺口。

## 已实现

- application 层 `SandboxProvider` / `SandboxInstance` 端口与 `SandboxManager`。
- macOS Seatbelt、Linux/WSL2 bubblewrap、原生 Windows/不可用平台 No Sandbox 候选。
- 启动期行为自测；Provider 存在但自测失败不会报告可用。
- 沙箱能力、Provider 版本和 policy hash 进入 `ExecutionProfile` 与授权画像。
- 单次实例临时工作区副本、实例私有临时目录、默认禁网和销毁清理。
- 沙箱运行时失败不降级；无沙箱不存在直接执行旁路。
- 只有文件系统隔离和临时层成立时，Shell 才能用 ephemeral binding 通过恢复门；直接
  写文件工具继续要求 RecoveryStore。
- 长参数规范化、简单 glob 目标冻结、三方言重定向 facts、间接执行递归预检。
- Python AST 与可注入跨语言信号表的脚本副作用分析，脚本正文 hash 绑定有效计划。
- auto 对无法完整解析的 Shell/脚本调用隔离的结构化 LLM Safety Classifier；仅
  `low + aligned + allow + 高置信度` 可以消除 UNKNOWN，失败与不确定统一 ASK。
- 分类报告发现的能力和副作用单调合并，报告 hash 进入 plan hash；Hard Deny 在分类器前拒绝。
- 人工“仅本次批准”在重验 plan/view hash 后签发一次性直接授权，不再因 RecoveryStore
  未配置而二次阻断；自动真实写入仍要求恢复绑定。

## 明确限制

- 当前 Seatbelt/bubblewrap 最多报告 Partial，不报告 Strong。
- bubblewrap 暂以宿主根只读挂载，不能声称隐藏所有可读文件。
- 原生 Windows 当前只有 No Sandbox。
- 临时工作区使用完整复制，尚未采用 reflink/overlay。
- `/sandbox` 和完整 `/add-dir --write/--remove` 会话策略 UI 尚未实现。
- ADR-0015 的真实工作区 RecoveryStore/materialize 尚未实现。
- 人工直接授权不提供恢复保证；审批 UI 会明确展示这一点。

## 验收命令

```text
make ci
poetry run forge --help
poetry run forge --version
```

关键回归覆盖 Provider 选择与单次销毁、运行期不回退、临时恢复绑定、长参数 root 删除、
glob 冻结、重定向、`find -exec`、inline 脚本写入、脚本 root 删除、分类器安全/失败/外部
副作用分支，以及人工批准后的 FileTool/ShellTool 直接执行。
