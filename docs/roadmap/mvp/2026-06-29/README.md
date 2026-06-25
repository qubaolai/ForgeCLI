# 2026-06-29：AgentTurnService Stub 与 REPL 解耦

## 今日目标

把 REPL 中直接处理自然语言输入的逻辑移入 `AgentTurnService` stub，让 CLI 只负责输入、输出和分派，为后续接入 workflow、LLM 和工具系统留出稳定边界。

## 需求更新

- 模式切换统一走斜杠命令处理路径：`/chat`、`/plan`、`/act` 解析为普通 `SlashCommand`，由对应 handler 调用 application service 更新 session state。
- 会话控制不再由自然语言退出词或 `/exit` 处理；交互式会话退出仅支持空行连续按两次 `Ctrl-C`。
- 空行 `Ctrl-D` 不作为会话退出入口；有输入内容时仍保留删除字符语义。
- 同一个项目根目录同一时刻只允许一个 `forge` 进程操作。启动进入 REPL 前必须获取项目级排他锁；锁文件落在用户级 Forge home 的项目目录下，不写入被操作仓库。
- 斜杠命令由 registry + handler 执行，REPL 不再承载模式切换、配置、状态等业务逻辑。

## 当前基线

前一工作日应已具备：

- session 事件写入。
- `state.json` 快照。
- `/status` 读取 session state。
- 自然语言和 slash command 已能区分记录。

## 开发指导

- 新增 `application/agent_turn` 或 `application/conversation` 模块。
- 定义 `AgentTurnService.handle_user_message(...)`。
- REPL 的 `UserMessage` 分支只调用 service 并渲染返回结果。
- service 当前返回固定 assistant stub，但要写入 `assistant_message` 事件。
- mode 从 session state 读取，不再只依赖 REPL 内存态。
- plan/act/chat 模式切换通过斜杠命令 handler 调用 session service 更新 state。
- `/exit`、`exit`、`quit`、`:q` 不再作为退出入口；退出只由 prompt 层的 `Ctrl-C×2` 产生 `QuitSignal`。
- 裸 `forge` 绑定项目后应获取项目级排他锁；获取失败时输出可理解提示并退出，不进入 REPL。
- 不接真实 LLM，不引入 AgentWorkflow 具体实现。

## 最终产物

- `AgentTurnService` stub。
- `AssistantResponse` 或等价返回 DTO。
- REPL 与自然语言处理解耦。
- `user_message` / `assistant_message` 事件成对落盘。
- `/chat`、`/plan`、`/act` 的斜杠命令 handler。
- 交互式退出策略：仅空行 `Ctrl-C×2`。
- 项目级进程排他锁。
- 单元测试覆盖 chat/plan/act 模式下的 stub turn。
- 单元测试覆盖斜杠命令统一路由、退出策略和项目锁互斥。

## 验收重点

- CLI 层不再直接 sleep 或拼接 assistant 文本。
- application service 不依赖 Rich、Typer、prompt_toolkit。
- 事件顺序稳定：user_message -> assistant_message -> state update。
- `/chat`、`/plan`、`/act` 通过 registry 解析为 `SlashCommand` 并由 handler 更新 mode。
- `/exit`、`/pause` 等未注册控制命令不得被当作内建控制信号执行。
- 空行 `Ctrl-D` 不退出会话，空行 `Ctrl-C×2` 才退出。
- 同一项目锁被占用时第二个 `forge` 进程不得进入 REPL。
