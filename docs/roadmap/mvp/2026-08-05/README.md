# 2026-08-05：自动压缩与预算

## 今日目标

实现基础 context budget 和自动压缩触发。

## 开发指导

- 根据配置读取 token budget 和阈值。
- 超过阈值时请求 compact。
- 自动 compact 不应覆盖人工摘要中的关键事实。

## 最终产物

- context budget 策略。
- 自动 compact 触发测试。
- 摘要保真测试样例。

## 代码验收

我会检查预算逻辑是否可配置，自动压缩是否写事件，是否不影响后续 resume。

