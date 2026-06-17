# 2026-07-29：Artifacts

## 今日目标

实现长输出和产物文件存储。

## 开发指导

- stdout/stderr 长输出写入 `artifacts/tool-output/`。
- 事件日志只保存摘要和 artifact 路径。
- artifact 路径必须在 session 目录内。

## 最终产物

- `ArtifactStore`。
- 长输出测试。
- artifact 路径安全测试。

## 代码验收

我会检查事件日志是否保持轻量，artifact 是否可追踪且不会写到 workspace 外。

