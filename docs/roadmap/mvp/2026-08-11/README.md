# 2026-08-11：MVP 最终验收

## 今日目标

完成 MVP 发布前验收，判断是否可以进入 Alpha 阶段。

## 开发指导

- 跑完整测试。
- 跑两个 E2E。
- 检查文档与实际行为一致。
- 检查 session、event、state、artifact、compact、resume。

## 最终产物

- MVP 验收报告。
- Alpha backlog。
- 已知问题和风险清单。
- 是否进入 Alpha 的建议结论。

## 代码验收

MVP 通过条件：

- `forge` 可启动本地会话。
- 支持 chat/plan/act。
- session 写入 `events.jsonl` 和 `state.json`。
- 可 resume。
- 可执行基础只读工具和测试工具。
- 高风险动作触发审批。
- 可 compact。
- 可 inspect/status。
- 核心测试通过。
