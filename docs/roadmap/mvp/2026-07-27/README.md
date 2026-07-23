# 2026-07-27：四模式收敛 + root 启动校验

## 今日目标

按 ADR-0009 §2/§6 把 `SessionMode` 收敛为 `PLAN/ACCEPT_EDITS/AUTO/FULL_ACCESS`（默认
`ACCEPT_EDITS`），退役 `chat`，并落地「拒绝以 root 运行」的启动校验。本日只动模式枚举与入口，
不实现裁决逻辑（留 07-29）。

## 开发指导

- `SessionMode` 由 `CHAT/PLAN/ACT` 改为 `PLAN/ACCEPT_EDITS/AUTO/FULL_ACCESS`，默认
  `ACCEPT_EDITS`；`/chat` 保留为兼容别名映射到 `accept_edits`，新增 `/accept-edits`、`/auto`、
  `/full-access` slash 命令。
- 同步 mode_command / status_command / 模式菜单 / 状态栏与 REPL 事件（mode_changed）。
- root 启动校验：检测到 root / 管理员身份时**拒绝启动**（非降级）并说明原因；Windows 对应管理员
  提权检测。校验位于 bootstrap，早于进入 REPL。
- 模式定义写死为代码级常量，不可由配置放宽（ADR-0009 §13）。

## 非目标

- 不实现 deny/ask/allow 裁决（留 07-29）；本日模式只改变 tool_catalog 能力门与默认档标签。
- 不接沙箱、不接工具。

## 最终产物

- 四模式枚举与 slash 命令、菜单、状态栏同步。
- root 启动校验及其单元测试（root 身份拒绝启动、普通身份放行）。

## 验收重点

- 默认模式为 `accept_edits`；`/chat` 别名可用；`SessionMode` 不再含 `CHAT`。
- 以 root / 管理员身份启动时拒绝运行并说明原因。
- 模式切换写入事件日志；模式定义无法经用户配置放宽。

## 验收命令

```bash
make ci
```
