# 2026-07-08：ModePolicyResolver

## 今日目标

把当前模式、配置和企业策略合并成最终执行策略。

## 开发指导

- chat 默认不写文件。
- plan 只读。
- act 允许写文件但高风险动作仍需审批。
- 策略必须能被工具执行前查询。

## 最终产物

- `ModePolicyResolver`。
- chat/plan/act 策略测试。
- 企业策略覆盖测试。

## 代码验收

我会重点检查 plan 模式是否无法写文件，act 是否不会绕过 destructive/external 审批。

