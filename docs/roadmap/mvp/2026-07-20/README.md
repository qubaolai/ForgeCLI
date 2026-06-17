# 2026-07-20：Tool Registry

## 今日目标

实现统一工具注册中心。

## 开发指导

- 支持注册、查询、列出工具。
- 工具必须有 `ToolSpec`。
- 工具暴露给 workflow 前要经过 ModePolicy 过滤。

## 最终产物

- `ToolRegistry`。
- 工具注册测试。
- `/tools` 可选草案。

## 代码验收

我会检查工具是否都通过 registry 暴露，是否没有直接在 workflow 中硬编码工具实现。

