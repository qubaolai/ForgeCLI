# 2026-08-04：Context Compact

## 今日目标

实现手动上下文压缩。

## 开发指导

- 支持 `/compact`。
- 生成 `summary.md`。
- 写入 `context_compacted` 事件。
- 摘要包含目标、已做动作、文件、风险、下一步。

## 最终产物

- compact 命令。
- summary 生成逻辑。
- compact 后继续对话测试。

## 代码验收

我会检查摘要是否足以恢复任务上下文，是否不会丢失用户约束和未完成事项。

