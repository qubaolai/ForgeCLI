# ForgeCLI

ForgeCLI 是本地优先、由浏览器控制的对话式软件工程 Agent。Agent Runtime、文件、Git、
受控 Shell、沙箱、审批、恢复和 session 数据全部留在本机；浏览器只提供项目、会话和控制界面。

## 开发运行

```text
poetry install
make web-install
make web-build
poetry run forge
```

裸 `forge` 会在 `127.0.0.1:8765` 启动本地服务并打印一次性启动链接；默认不自动打开浏览器。
端口固定，重启 Forge 后已经打开的页面会自己连回来。需要自动打开或换端口时：

```text
poetry run forge --open --port 8899
```

## 终端会话

开不了浏览器 (SSH, tmux) 或者只想顺手问一句时，用终端入口：

```text
poetry run forge --cli
```

`make run-cli`是同一条命令的快捷方式。它需要一个真正的终端：stdin/stdout 被重定向时
forge 会直接拒绝启动 (退出码 4)，而不是挂在那里等一个永远不会来的回答。

它和 Web 是同一个 Forge：同一份装配、同一把项目锁、同一套裁决与审批语义，只是交互在终端里。
直接输入文字就是和模型说话。

- 输入 `/` 弹出命令菜单（命令名 + 说明），`↑↓` 选，回车确认，Esc 关闭；命令多于一屏时
  菜单自己滚动。`/help` 列出全部，`/<命令> -h` 看某一条怎么用。
- 这些命令与 Web 上的面板一一对应：`/mode`、`/model`、`/provider`、`/config`、`/dirs`、
  `/plan`、`/checkpoints` 等。`/config` 按前缀分类，`←→` 换分类。
- 菜单没打开时 `Tab` / `Shift+Tab` 沿权限梯度切换模式 (manual -> plan -> accept_edits -> auto), 当前模式显示在输入框右下角. `full_access` 只在启动时带了 `--allow-full-access` 才可选, 第一次要在终端里确认一次; 应用级配置 `security.disable_full_access` 能把它锁死.
- 各级菜单和审批卡片都能用 `↑↓` 选、数字键直选，`Esc` 退回上一层。
- `Ctrl+J` 换行，`↑` 翻历史，`Ctrl-C` 停止当前一轮或取消当前这一步，空行连按两次退出。

同一个项目同时只能有一个 Forge 进程，所以 Web 和 `--cli` 不能同时开着同一个项目
(后启动的一方会告诉你占用者的 pid)。详见 [ADR-0045](docs/adr/2026-09-03-0045-恢复终端交互式入口并与Web控制面共存.md)。

## 子 Agent 调研

主 Agent 可调用 `Agent`，把独立的代码搜索、阅读和分析任务交给子 Agent，再汇总报告。
子 Agent 使用独立上下文，只能使用 Read、Glob、Grep、LSP、WebSearch、WebFetch 和可证明只读的 Bash；
不修改工作区、不询问用户，也不能继续创建子 Agent。报告中的证据由主 Agent 复核后再用于改动。

缺省每轮最多派发 8 次、同时运行 4 个，每个子任务最多 30 次循环模型调用，300 秒后请求取消。
可通过以下环境变量覆盖（正数）：

- `FORGE_MAX_SUB_AGENTS_PER_TURN`
- `FORGE_MAX_SUB_AGENT_CONCURRENCY`
- `FORGE_SUB_AGENT_MAX_MODEL_CALLS`
- `FORGE_SUB_AGENT_TIMEOUT_SECONDS`
- `FORGE_SUB_AGENT_GRACE_SECONDS`（停止收尾提示阈值，缺省 30 秒）

停止父轮会取消全部子任务。超时采用协作取消；如果底层调用尚未退出，界面显示正在停止，
项目仍保持忙碌直到收尾完成。子任务不是可跨轮运行的后台任务。模型设置可通过 `sub_agent`
用途绑定独立模型；压缩仍使用 `compact` 用途，用量均计入父轮。

## 联网搜索

`WebSearch` 通过 Parallel MCP 搜索网页，查询会发送给 Parallel。默认支持匿名搜索，
结果数量由服务端决定。在启动 Forge 前设置环境变量 `PARALLEL_API_KEY` 可启用认证；
此时工具会提供 `num_results` 参数（1–10，默认 5）。不预设搜索地区。
只读子 Agent 也可使用，现有工具权限规则可控制或禁用 `WebSearch`。

调用总时限为 25 秒，支持取消；响应读取上限为 1,000,000 字节。
超长文本复用工具框架的结果落盘机制。搜索摘录保留来源链接，不保证实时行情。

`WebFetch`（别名 `web_fetch`）接收 `url`，由本机直接 GET 公开网页，再在本地转为
Markdown；不调用 Parallel，也不需要 API Key。已有 URL 可直接读取，WebSearch 摘录
不足时再补读。清理脚本、导航、广告和隐藏元素，优先提取 main/article，保留标题、
链接、列表、表格和代码块；图片只保留替代文本，不下载图片或执行 JavaScript。
支持 HTML、Markdown 和纯文本；不支持 PDF、二进制下载、登录页面和必须执行 JS 的页面。

只允许 HTTP(S) 的 80/443 端口。拒绝本地/非公网地址、URL 用户名密码和常见凭证参数；
DNS 只在权限通过后执行，所有解析结果均须通过公网检查，连接固定到已验证 IP，并保留
原域名的 Host、TLS SNI 和证书校验。每次重定向重新检查，最多 5 跳，拒绝 HTTPS 降级。
不继承环境代理、不使用浏览器登录态、不跨跳转传递 Cookie；网络总时限 25 秒，响应传输
与解压后的正文各最多 2,000,000 字节，支持取消。超长 Markdown 复用工具框架落盘。
这些检查不等于内容审核：尚未接入独立审核分类器，网页内容仍是不可信资料。

## Bash 与结构化输出

Bash 按 `;`、换行、`&&`、`||` 和管道分别检查分析单元，实际仍完整执行原命令。
工作区内的目录切换和只读查询可自动放行；分号后会检查目录切换成功和失败两种路径。
无法静态验证的控制结构、越界路径、写操作及显式拒绝规则仍按权限流程处理。
`2>/dev/null` 可用于丢弃错误输出，普通文件重定向不属于只读操作。

模型设置区分两项能力：`supports_structured_output` 表示原生 JSON Schema 输出，
`supports_json_object` 表示 JSON Object 输出。二者默认关闭；都关闭时，结构化调用使用
提示词约束和本地 schema 校验。仅支持 JSON Object 的接口应关闭前者、开启后者。
两项均开启时优先使用 JSON Schema。服务端明确返回“不支持该 response_format”的 400
错误时，会在同一模型上降级到声明支持的 JSON Object 或文本校验通道；取消、其他请求
错误及 schema 不合法不会被这种降级掩盖。所有通道都校验最终 JSON 的字段和类型。

## 排查问题

日志、工具输出、诊断和本进程的文件撤销备份默认统一放在系统临时目录中的
`forge-<用户标识>/run-<随机标识>/`，按 `logs/`、`projects/` 等子目录分开。
每个进程有独立目录；启动时只清理超过 7 天且没有活跃进程占用的日志、中间产物、
工具结果等明确列出的临时子目录，退出时刷新保留期。不删除整个运行目录、项目目录
或会话目录，未列入清理范围的文件一律保留。
日志实际路径可通过 `/diagnostics` 或 `GET /api/v1/diagnostics` 查看。
显式设置 `logging.directory` 仍会使用指定目录，不参与默认临时目录的回收。

配置、会话历史（含处理过程）、记忆、计划和持久恢复快照继续保存于 Forge home。
临时产物可能被系统提前清理，旧会话里的产物路径不保证永久可读。已有 `.forge` 内容
不会被自动搬迁或删除，避免误删旧会话仍在引用的数据。

默认级别 `info` 记录一次运行的骨架 (启动, turn 起止, 模型调用, 工具裁决与执行结果);
把配置项"日志级别"(`logging.level`) 调成 `debug` 会额外写整段提示词, 模型回复原文,
工具输出与 HTTP payload —— 在控制面设置页的"常规配置"里改, 下次启动生效.
`GET /api/v1/diagnostics` 给出日志位置, 各阶段耗时分位数与模型调用读数.

日志**不脱敏** (凭证的值除外), 贴给别人之前请先自己看一眼. 详见
[docs/使用相关.md](docs/使用相关.md) 与 ADR-0035.

## 质量检查

```text
make ci
```

该命令检查 Python lint/format/mypy/架构依赖、React/TypeScript 类型与生产构建，并运行
pytest。会话与项目状态继续保存在用户级 `~/.forge/projects`，不污染工作区。
