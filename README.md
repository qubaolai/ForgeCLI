# ForgeCLI — session-storage slice (claude_test)

2026-06-27 切片：会话事件存储骨架与状态快照（`events.jsonl` + `state.json`），
惰性创建、写入顺序为事件先落再更新快照。仅含自包含的 `application/session` 与
`infrastructure/session`，不含 REPL / CLI 接线。
