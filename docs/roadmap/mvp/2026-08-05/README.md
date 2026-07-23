# 2026-08-05：切片 1 集成验收与文档收口

## 今日目标

跑通 Agent 主循环 + 权限引擎（rule-engine-only）的完整集成矩阵与安全边界测试，确认 07-23 至
08-04 的实现可作为切片 1 交付；同步 ADR、详细设计、路线图与 backlog。

## 开发指导

- 集成矩阵：四模式（plan / accept_edits / auto / full_access）× 各类动作（只读 / 文件编辑 /
  文件操作命令 / 本地命令 / git 写 / 出区读写 / 高危）逐格验证裁决结果。
- 安全边界（绕过用例）：`git status && rm -rf ~`、`echo "$(rm -rf ~)"`、`timeout rm -rf ~`、
  `devbox run rm -rf .`、经 `..`/symlink 写出工作区——均须被正确拦截，且有测试。
- `auto` 无人值守最小策略：审批超时转 observation 交回循环，收尾列出被跳过动作（完整预算按
  任务计算留后续）。
- 补齐边界测试：`AgentLoop` 不 import CLI/fs/shell/git/SDK；副作用只经 `AgentTurnService`；
  高危 deny 先于执行拦下；deny 恒压过 allow。
- 文档收口：ADR-0009 / ADR-0010、详细设计、路线图（本切片标记完成）、backlog 同步；记录
  「本切片 rule-engine-only、沙箱在切片 2」的阶段取舍。

## 非目标

- 不接 Bash 沙箱（切片 2）；本切片以 rule-engine-only 交付，这是无沙箱平台的合法状态。
- 不接 MCP / Skills / Sub-Agent / 上下文压缩。

## 最终产物

- 完整集成测试矩阵 + 安全边界（绕过用例）测试。
- 切片 1 验收报告与切片 2（沙箱）任务清单。
- ADR / 详细设计 / 路线图 / backlog 同步。

## 验收重点

- 四模式 × 各类动作裁决与 ADR-0009 一致，可复现。
- 所有绕过写法被拦截并有对应测试。
- 单轮到多步 ReAct（读→改→跑→再改）在 accept_edits/auto 下可跑通，副作用全部落盘可 resume。
- 默认无网络测试通过；文档口径一致。

## 验收命令

```bash
make ci
poetry run forge --help
poetry run forge --version
```
