# 2026-07-14：BuiltinWorkflow Chat

## 今日目标

实现 `BuiltinWorkflow` 的 chat turn。

## 开发指导

- chat 模式优先回答和解释。
- 不自动写文件。
- 模型响应写入 assistant message event。

## 最终产物

- chat workflow。
- chat turn 集成测试。
- assistant message 落盘测试。

## 代码验收

我会检查 chat 是否保持保守行为，是否没有隐式调用高风险工具。

