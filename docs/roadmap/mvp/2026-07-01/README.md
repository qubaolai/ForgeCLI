# 2026-07-01：EventStore

## 今日目标

实现 append-only 的 `events.jsonl` 存储。

## 开发指导

- 支持追加事件、读取事件、按 session replay。
- 每个事件包含 `event_id`、`session_id`、`type`、`created_at`、`payload`。
- 长输出不直接写入事件 payload。

## 最终产物

- `EventStore`。
- 事件读写测试。
- 损坏行处理策略。

## 代码验收

我会检查事件是否 append-only，是否能 replay，是否避免把大 stdout 或敏感信息直接塞进事件。

