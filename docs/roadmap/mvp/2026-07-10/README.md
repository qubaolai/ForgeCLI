# 2026-07-10：ModelProvider 抽象

## 今日目标

定义模型调用抽象，并接入一个最小可替换 provider。

## 开发指导

- `ModelProvider` 不暴露具体 SDK 类型到 application 层。
- 支持普通文本响应。
- 工具调用可以先定义接口，后续接入。

## 最终产物

- `ModelProvider` port。
- 一个 mock provider。
- provider 错误处理测试。

## 代码验收

我会检查模型 provider 是否可替换，测试是否不依赖真实网络或真实 API key。

