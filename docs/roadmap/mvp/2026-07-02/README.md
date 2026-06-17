# 2026-07-02：StateStore

## 今日目标

实现 `state.json` 快照和原子写入。

## 开发指导

- `state.json` 可快速恢复 session。
- 写入使用临时文件加 rename。
- 恢复时以 `events.jsonl` 为审计源。

## 最终产物

- `StateStore`。
- 原子写入测试。
- state 与 event 不一致时的恢复策略测试。

## 代码验收

我会检查 state 是否只是快照而非真相源，恢复逻辑是否能处理中断写入。

