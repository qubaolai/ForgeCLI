# 2026-07-03：SessionService 与第一阶段集成

## 今日目标

打通 session 创建、打开、暂停和恢复的应用服务。

## 开发指导

- 实现 `SessionService`。
- 创建 `.forge/sessions/<session_id>/`。
- session 创建时写入 `session_created` 事件和初始 state。

## 最终产物

- session 生命周期服务。
- 第一阶段集成测试。
- M1 验收报告。

## 代码验收

我会检查本地目录结构、事件和 state 是否一致，session 创建失败是否不会留下半成品状态。

