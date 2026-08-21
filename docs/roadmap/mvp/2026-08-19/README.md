# 2026-08-19：本地 Web 控制面切片

## 目标

按 ADR-0025 将用户交互从 Rich/TTY 迁移到本地 Web，同时保持 AgentLoop、工具、安全、恢复和
session 语义不变。裸 `forge` 只启动 loopback 服务并打开浏览器。

## 本次交付

- FastAPI/Uvicorn 本地服务、一次性启动 token、session cookie、CSRF、Origin/Host 与 CSP。
- React/TypeScript/Vite 项目中心、会话列表、流式对话时间线、可调宽计划、内联审批和设置界面。
- 项目激活与进程锁、单活动 turn、取消、session 新建/恢复和 transcript 查询。
- `AgentRunEvent` 到 SSE 的有限缓冲与断线 resync 协议。
- Web 工具审批 broker 和计划评审接口；断连/超时不自动批准。
- 模式、配置、模型、工具、工作区授权、学习规则、恢复点和 undo/restore API。
- `InputOrigin.WEB_USER`；Web 中 `#` 永远不触发人工 Shell。
- LLM/tool wiring 移到 `interfaces/runtime`，不让 Web 依赖 CLI 展示适配器。
- 用户消息乐观上屏；模型正文、工具分组、token 与耗时由 SSE 增量渲染，完成后自动折叠过程。
- IME Enter 防误发、140px 滚动跟随阈值、可取消项目选择和本地 Markdown 计划视图。
- 聊天回复与计划正文渲染安全 Markdown；计划默认关闭，项目选择支持项目中心布局与 Esc 返回。
- 运行过程采用无边框事件时间线，展示中间模型正文、用量和可二次展开的工具调用。
- `make run` 显式接管 SIGINT/SIGTERM，单次 Ctrl-C 可完成服务与项目锁清理。
- SSE 流在服务停止和项目切换时自行收尾；浏览器挂着连接时 Ctrl-C 不再卡住也不再打印异常栈。
- 控制面校验改为纯 ASGI 中间件；事件流合并为单一 `run_event`，首连不重放历史缓冲。
- 顶栏展示真实连接状态，输入框自动增高，运行态由 `/turns/current` 恢复，设置面板支持 Esc/点击遮罩关闭。
- 默认不再自动打开浏览器，启动链接常驻输出；`--open` 显式打开。
- SSE 重连改为页面自管的指数退避，超过一小时间隔停手并提供手动重连；会话失效会直接说明如何恢复。
- 运行过程改为步骤时间线，模式选择改为自定义下拉，代码块自带复制按钮，最终用量改为 chip 展示。
- 端口默认固定 8765，session/CSRF 密钥跨重启复用；重启 Forge 后已打开的页面自动连回，无需手动重连。
- 处理过程去掉类型图标、终态自动折叠，并可在刷新后经 `/api/v1/runs` 重新展开。
- 修掉流式渲染的平方级开销：长回答不再卡住页面（4000 条增量从 16.6s 阻塞降到 3ms）。
- 全部 Forge 持久化改为 JSON（ADR-0026），删除 tomlkit 依赖与全部 TOML 读写代码。
- 计划与待办由模型按任务命名（ADR-0022 决策 10），目录名不再是随机短 id。
- Web 补齐 CLI 的全部配置面（ADR-0025 决策 32）：当前模型、用途覆盖、thinking、工具清单、恢复层状态、会话状态、计划目录与待办。
- 修复流式渲染崩溃：Markdown 解析器遇到 `1. ` 这类中间态会死循环并无限 push，标签页被 OOM 杀掉。
- 事件批量落盘由 rAF 改定时器（切走标签页时 rAF 不触发）；入口 HTML 改 no-cache，产物长缓存。
- 处理过程运行中可折叠；模型与思考强度移到输入框旁；模型配置改为整表单一次保存；侧栏长路径 hover 显示全文。
- 未执行的工具调用（工具不存在、prepare 失败、被拒、未获批准）现在也发终态事件并写审计，不再停在"未完成"。
- Web 审批去掉 5 分钟等待超时；停止本轮与服务退出才会放开等待，绝不自动批准。
- 处理过程运行时展开、答案落地即收起；工具调用按读/写/执行/计划分类聚合相邻调用，分类
  依据是这次调用声明的能力而不是工具名。
- 模型调工具之前说的话一次都不进聊天区：只有确认这次调用不再要工具，正文才交给最终回答。
- 文件工具补齐：新增 `fs.scan_tree` 与 `fs.create_file`，`fs.write_patch` 改名
  `fs.edit_file` 并只做替换；`fs.read_file` 支持 `offset` / `limit` 按行读。
- `fs.edit_file` 的片段定位容忍行尾空白、CRLF 与整块统一的缩进偏移，并把同样的偏移施加到
  替换文本上；缩进方式不同（制表符对空格）仍然拒绝，但会把文件原文带行号引回给模型照抄。
- 运行事件补细节，便于排查：排队事件带原始入参，准备事件带目标路径与归属，裁决事件带命中
  规则与风险事实，终态事件带错误码 / 退出码 / 字节数 / 是否真的执行过。
- 聊天 Markdown 提供内外双复制入口、清晰的行内代码配色和最终回复用量/耗时摘要。
- 前端静态资源进入 Python wheel；Web typecheck/test/build 纳入 `make ci`。
- 重构内置工具不变量：schema 数值/长度约束真实生效；计划 ID 防路径穿越；读写前复核对象
  状态；文件写入/移动 no-replace 且保留 mode；目录与移动两端可完整 undo；所有扫描、输出和
  artifact 截断显式可见；Git 只读工具关闭网络提示、pager、锁、fsmonitor 与外部 diff。
- 新增 `fs.create_directory`；文件创建和移动不再顺带创建未声明的父目录。
- 安全链按 ADR-0027 收敛为单一事实来源：Shell 只解析一次，路径保护统一走 realpath，审批
  视图并入绑定；环境、cwd、脚本和可执行文件在 perform 前统一复核，审批响应不得串用 id
  或扩大 scope，隐式用户级工具配置由执行画像关闭。

## 验收

```text
make ci
poetry run forge --help
poetry run forge --version
poetry build
```

另做真实 loopback 启动检查：`forge --port 8765`，验证健康检查、boot token 换取
cookie、项目中心静态资源和 Ctrl-C 释放项目锁。浏览器保持打开时再按一次 Ctrl-C，服务应在
1 秒内退出且不打印异常栈。

## 已知边界

- MVP 只监听 loopback，不提供远程或多人访问。
- 同一时刻只激活一个项目、每项目只运行一个 turn。
- 不提供 Web PTY；原人工 Shell 产品入口由 ADR-0025 取代。
- 额外工作区目录重启后按只读恢复；写授权需由用户重新显式确认。
- 当前 SSE 环形缓冲只保证进程内重连，持久化恢复仍以 `events.jsonl + state.json` 为准。
- macOS 受限测试环境中 `/bin/ps` 被拒绝，既有 PTY 进程组 E2E 会失败；这不是 Web 改造回归。
- ADR-0004 §14 列出的 `git.write` 至今没有实现，组合根里也没有它；表里已标注"未注册"。
  git 写操作目前仍只能走 `shell.run`。
