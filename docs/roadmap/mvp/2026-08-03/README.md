# 2026-08-03：ResumeService

## 今日目标

实现 session 恢复能力。

## 开发指导

- `forge resume` 默认恢复最近 active session。
- `forge resume <session_id>` 恢复指定 session。
- 从 state 快速恢复，并可用 events 校验。

## 最终产物

- `ResumeService`。
- resume 命令。
- 中断恢复测试。

## 代码验收

我会检查恢复后模式、计划、摘要和最近消息是否一致，不能重复执行已完成的高风险工具。

