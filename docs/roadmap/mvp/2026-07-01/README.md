# 2026-07-01：周集成验收、交互式 Resume 与工具系统准备

## 今日目标

把本周新增的单入口 CLI、配置、模型目录、session store 和 AgentTurn stub 做一次集成收敛，并补上最小 `/resume` 交互式入口，为下一阶段 Agent ReAct 主循环与统一 LLM 调用架构准备稳定边界。

## 开发指导

- 梳理本周模块边界：
  - `interfaces/cli`
  - `application/slash_commands`
  - `application/session`
  - `application/models`
  - `application/config`
  - `application/llm/config`
  - `application/agent_turn` 或 `application/conversation`
  - `infrastructure/config`
  - `infrastructure/llm/config`
  - `infrastructure/session` 或 `infrastructure/storage`
  
- 新增 `/resume` slash command stub：
  - 无参数时提示最近 active session。
  
  - `/resume <session_id>` 可读取 state 并展示摘要。
  
  - 支持分页恢复历史记录, 按事件渲染, 需要考虑不同类型事件的渲染方式可能不同
  
    比如普通对话事件 llm流式响应事件 工具调用事件 sub agent相关事件, 本次只考虑先实现对话事件的渲染即可, 但需要为后续扩展新实践增加支持
  
- resume 能力复用 application service，不在 Typer 层新增 `forge resume`。

- 补一份本周集成测试，覆盖：
  - `/config` 配置修改路径
  - `/model`
  - 裸 `forge` 启动 session
  - user turn 写入事件
  - `/status` 读取 state
  - `/resume` 读取 state 摘要
  
- 更新 roadmap 中的遗留风险和下一周建议：下一开发切片优先做 Agent ReAct 与 LLM Provider 架构落地，工具系统与审批进入后一阶段 backlog。

## 非目标

- 不实现工具执行。
- 不实现审批。
- 不实现 context compact。
- 不实现真实 LLM。
- 不新增 Typer 业务子命令。

## 最终产物

- 本周集成测试。
- `/resume` 最小交互式入口。
- Agent ReAct 主架构设计。
- 多供应商 LLM 调用主架构设计。
- 后续工具系统和审批 backlog。

## 验收命令

```bash
make ci
poetry run forge --help
printf '/status\n/resume\nexit\n' | poetry run forge
```

如果 TTY 限制导致管道无法完整驱动 REPL，应以 REPL、session service 和 resume service 的集成测试作为主验收。
