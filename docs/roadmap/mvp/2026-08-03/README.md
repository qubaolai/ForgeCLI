# 2026-08-03：shell 工具 + ApprovalService + 学习式授权

## 今日目标

按 ADR-0009 §9 接入 shell 工具（经规则引擎裁决）与 `ApprovalService`，落地学习式授权
（once / always / session / deny）。本日把「跑命令」这条路径按四模式跑通，并让 `accept_edits`
的命令询问可累积成 allow。

## 开发指导

- shell 工具：命令先经 07-28 解析器规范化，再经 07-29 引擎裁决（deny/ask/allow），本地执行
  （沙箱留切片 2）。
- `ApprovalService`：ask 命中时向用户询问，四档结果——`once`（仅本次）、`always`（写项目级
  配置成 `Bash(...)` allow 规则）、`session`（本会话内存不落盘）、`deny`（拒绝转 observation）。
- 学习式授权归一化键 = `可执行文件 + 第一个子命令`（`npm test`、`git status`）；参数不进键但仍
  过 deny；复合命令为每个需批准子命令各存一条。
- `approval_resolved` 事件记录 scope，供审计与 resume。
- `auto` 预设：命令执行（含 git 写）自动放行，动区外仍 ask；`full_access`：再放行动区外。

## 非目标

- 不做静态命令白名单闸门（学习式授权替代）；种子 allow 探测留 08-04。
- 不接沙箱；命令本地执行，安全靠规则引擎 + OS 非 root。
- 不实现 auto 的无人值守超时转 observation 完整策略（留 08-05 集成时补齐最小版）。

## 最终产物

- shell 工具 + `ApprovalService` + 学习式授权（四档 scope）。
- `always` 落项目级配置为 allow 规则；`session` 只存内存。
- 四模式命令裁决与授权累积的集成测试。

## 验收重点

- `accept_edits` 下每条命令都确认，`auto` 下命令直接跑、git 写不特殊拦、动区外仍 ask。
- 新命令（如 `tree`）首次询问，选 `always` 后不再问；`session` 不写配置文件。
- 经 shell 的 `git push` 与经 git 工具的 `git push` 得到相同裁决。

## 验收命令

```bash
make ci
```
