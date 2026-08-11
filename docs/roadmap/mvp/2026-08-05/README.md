# 2026-08-05：Agent Shell 安全机制集成验收与收口

## 今日目标

完成 ADR-0013/0014 的端到端接线、跨 mode 和跨平台验收，确认 Agent Shell 执行安全机制可以作为
5 个工作日 MVP 切片交付。

## 今日范围

- 接通：LLM ToolRequest → 能力门 → Shell AST → 规则决策 → 分类器（必要时）→ ShellTool →
  SandboxProvider / SandboxInstance 或 NoSandboxProvider → Shell 进程。
- 接通 `policy_denied`、`tool_unavailable_in_mode`、`approval_required` 和执行结果回填。
- 验收四种 mode：plan、accept_edits、auto、full_access。
- 验收三种环境：strong sandbox、partial sandbox、no sandbox。
- 验收普通命令、复合命令、heredoc、测试脚本、网络脚本和 Hard Deny。
- 验收临时脚本、heredoc、`-c` 内联脚本的确定性静态分析，以及分析不完整时的安全降级。
- 验收 `/sandbox` 默认关闭、用户启用/关闭、自测失败和仅影响后续执行实例的语义。
- 验收 `/add-dir` 增加、列出、写授权、撤销和策略版本变更。
- 完成审计事件、文档、测试和已知限制收口。
- 执行 `docs/security/2026-07-30-Agent-Shell安全红蓝对抗测试方案.md` 中的跨平台 P0/P1 用例。

## 非目标

- 不新增通用工具系统能力。
- 不扩展 MCP、Skills、Sub-Agent 和企业治理功能。
- 不以“分类器认为安全”替代 Hard Deny 或沙箱隔离。

## 最终产物

- 端到端安全执行测试矩阵。
- 安全回归语料和失败策略测试。
- ADR-0013/0014 与 roadmap 的一致性检查。
- MVP 已知限制清单和后续 backlog。

## 验收标准

- plan mode 不向 LLM 暴露 ShellTool。
- ShellTool 不包含安全决策逻辑，所有请求经过统一协调器。
- 复合命令整体预检，未授权命令不会产生部分执行。
- 强沙箱 auto 可自动执行受控脚本。
- 无沙箱 auto 需要分类器，分类器失败时 ASK。
- ASK 未经人类确认不调用 ShellTool；分类器失败、超时和非法输出均阻塞。
- 红蓝对抗中的受保护路径、Windows 方言、always 规则、沙箱失败和提示注入用例全部通过。
- 明显危险脚本可不经 LLM 直接 DENY；静态分析不得把“未发现危险”解释为无沙箱 ALLOW。
- SRT Provider 的 macOS experimental、Windows Alpha 和未安装/不可用回退均有明确结果。
- `/add-dir` 只能由用户显式授予，默认只读。
- Hard Deny 在所有 mode 和平台档案下均生效。
- `make ci` 通过，安全测试和文档验收完成。

## 关联决策

- ADR-0013。
- ADR-0014。
- `docs/security/2026-07-30-Agent-Shell安全红蓝对抗测试方案.md`。
