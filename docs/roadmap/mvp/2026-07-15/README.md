# 2026-07-15：BuiltinWorkflow Plan

## 今日目标

实现 plan 模式的只读规划能力。

## 开发指导

- plan 模式可生成 `Plan`。
- plan 模式不得返回写文件 ToolRequest。
- plan 更新写入事件。

## 最终产物

- plan workflow。
- plan 创建和更新测试。
- plan 只读策略测试。

## 代码验收

我会检查 plan 是否结构化，是否包含验收标准，是否不会把执行动作混入规划阶段。

