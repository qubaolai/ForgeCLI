# 2026-07-23：Shell 工具

## 今日目标

实现受控 shell 执行工具。

## 开发指导

- `shell.run` 支持超时、工作目录、stdout/stderr 捕获。
- 命令风险分类至少区分 readonly/write/network/destructive/external。
- 无法分类时按高风险处理。

## 最终产物

- shell 工具。
- 超时测试。
- 风险分类测试。

## 代码验收

我会检查 shell 是否默认受限，是否有超时，是否不会静默执行 destructive 命令。

