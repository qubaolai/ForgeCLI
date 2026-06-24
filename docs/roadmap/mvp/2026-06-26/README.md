# 2026-06-26：目录信任、用户级项目存储与工作区列表

## 今日目标

落地 ADR-0008 的 MVP 边界：首次在目录启动 `forge` 时确认信任，信任后把该目录作为项目根目录；项目状态存储在用户级 Forge home 下，不污染用户仓库；工作区从单个可修改路径改为可添加、可删除的目录列表。

今天只实现目录信任、用户级项目索引、项目配置骨架、工作区列表和交互入口。27 日再实现会话事件存储骨架与状态快照。

## 当前基线

前一日应已具备：

- 单入口 CLI 基线通过。
- 裸 `forge` 进入可退出 REPL。
- Typer 层只承担根入口、`--help` 和 `--version`。
- `/config` 与 `/model` 使用交互式菜单。
- 菜单按键语义已经收敛：`Enter` 进入/编辑，`←/→` 只切换开关或枚举。

## 26 日必须开发

### 1. 区分应用配置与项目配置

- 新增或调整配置路径解析，明确两类存储：
  - Forge home：默认 `~/.forge`，当前实现可继续支持 `FORGE_CONFIG_DIR` 覆盖。
  - 应用配置文件：Forge home 下的 `config.toml`、`llm.toml` 等跨项目配置。
  - 项目索引：Forge home 下的 `projects/index.toml`。
  - 项目配置目录：Forge home 下的 `projects/<project-id>/`。
- 应用配置继续保存跨项目偏好，例如主题、日志级别、LLM 供应商配置和模型声明。
- 项目配置保存当前项目相关配置，例如运行时默认模型、工作区目录列表、信任标记。
- 运行时默认模型跟随当前项目，避免在其它目录启动 `forge` 并切换模型时影响正在进行的任务。
- 27 日开始的 session state、`events.jsonl`、对话内容和 artifacts 必须写入 `projects/<project-id>/` 下，而不是应用配置文件。
- Forge 不在被信任项目根目录下自动创建 `.forge/`。
- 更新配置相关命名和注释，避免把 Forge home、应用配置、项目索引和项目配置目录混为一个概念。

建议落盘结构：

```text
~/.forge/
  config.toml
  llm.toml
  projects/
    index.toml
    ForgeCLI-a1b2c3d4/
      forge.toml
      sessions/
      artifacts/
```

### 2. 首次启动目录信任

- 裸 `forge` 启动后、进入 REPL 前，规范化当前目录路径。
- 读取 `projects/index.toml`，查找已信任项目中 `primary_workspace_root` 是当前目录自身或父级目录的项目。
- 如果命中多个项目，选择 `primary_workspace_root` 路径最长的项目。
- 额外添加的 `workspace_roots` 只表示 Agent 可操作目录，不参与启动时的项目身份匹配。
- 如果已有可信项目命中，直接绑定该项目，不再重复询问信任。
- 如果没有可信项目配置，提示用户是否信任当前目录。
- 提示内容必须展示当前目录的绝对路径。
- 用户选择“否”时直接退出，不进入 REPL，不创建项目配置，不执行任何后续动作。
- 用户选择“是”时：
  - 当前目录成为项目根目录。
  - 只信任当前目录，不自动信任父级目录。
  - 根据目录名和规范路径 hash 生成 `project-id`。
  - 在 Forge home 下创建 `projects/<project-id>/`。
  - 写入 `projects/<project-id>/forge.toml`，记录已信任、`primary_workspace_root` 和 `workspace_roots`。
  - 更新 `projects/index.toml`，记录规范路径到 `project-id` 的映射。
  - `workspace_roots` 初始值只包含 `primary_workspace_root`。
- 后续在该项目根目录或子目录启动 `forge` 时，应通过索引命中原项目，不再提示。
- 非 TTY 场景下不得卡住等待交互；测试可通过注入 prompt/fake prompter 覆盖选择结果。

### 3. 工作区改为列表

- 不再支持修改单个“工作区目录”。
- 从 `/config` 菜单移除“工作区目录”选项。
- 工作区目录只能通过显式命令添加或删除; 删除操作可以先不实现。
- 项目配置中使用 `workspace_roots` 列表保存可操作目录。
- `primary_workspace_root` 是首次信任目录，不允许删除。
- 路径保存为绝对规范路径。
- 重复添加同一目录直接无感跳过。

### 4. 新增 `/add-dir` 命令

- 新增会话内 slash command：`/add-dir`。
- `/add-dir` 打开交互式目录添加面板，不实现 Typer 子命令。
- 面板中提供一个输入框，用于输入要添加的目录路径。
- 输入框下方展示当前目录的子目录列表，帮助用户选择或参考。
- 子目录列表只展示目录，不展示普通文件。
- 输入可以是绝对路径，也可以是相对当前工作目录的路径；提交后必须规范化为绝对路径。
- 提交路径必须存在且是目录；非法路径显示友好错误，不抛 traceback。
- 添加成功后更新项目配置中的 `workspace_roots`，并显示已添加目录。

### 5. `/status` 展示 cwd 工作区列表

- `/status` 必须展示当前模式。
- `/status` 使用 `cwd:` 字段展示工作区目录列表，而不是单个当前目录。
- `cwd:` 至少包含 `primary_workspace_root`。
- 多个目录可以按多行或紧凑列表展示，但必须可读。
- `/status` 不要求展示 session id、event id 或读取 state 文件；这些属于 27 日。

## 交互设计

启动信任确认：

```text
是否信任当前目录？
/path/to/current/repo

[是] [否]
```

`/add-dir` 面板：

```text
添加可操作目录

输入目录路径: ...

当前目录下的子目录:
- docs
- src
- tests
```

`/status` 示例：

```text
mode: chat
cwd:
- /path/to/project
- /path/to/extra-repo
```

## 设计取舍

- 只做项目配置、信任标记和工作区列表，不做真实文件读取、写入、搜索或 shell 工具。
- 不实现 session event store；27 日在 `projects/<project-id>/` 下接入 `events.jsonl` 和 `state.json`。
- 不允许 Agent 通过自然语言、模型输出或隐式推断扩大可操作目录。
- 不把目录信任写入用户仓库；信任属于用户级项目索引和项目配置。
- 不把额外 `workspace_roots` 当作项目根查找依据，避免添加目录后改变“进入目录即进入项目”的语义。
- 不在 `/config` 中编辑工作区目录，避免形成配置菜单和工作区命令两条修改路径。

## 最终产物

- ADR-0008 已落地到 26 日实现。
- 首次启动目录信任确认。
- 已信任项目再次启动不重复询问。
- 在已信任项目子目录启动不重复询问。
- Forge home、应用配置、项目索引和项目配置路径清晰分离。
- 首次信任不会在项目根目录下创建 `.forge/`。
- `/config` 移除“工作区目录”选项。
- 当前项目维护 `workspace_roots` 列表。
- 运行时默认模型写入当前项目的 `forge.toml`。
- `/add-dir` 可添加可操作目录。
- `/status` 的 `cwd:` 展示工作区列表。
- 对信任选择、拒绝进入、重复启动、子目录启动、不创建项目根 `.forge/`、添加目录、非法目录、`/config` 去除工作区项和 `/status cwd` 有测试覆盖。

## 验收命令

```bash
make ci
poetry run forge --help
poetry run forge --version
```

信任确认和工作区目录管理以单元测试和 CLI fake prompter 测试作为主验收；如果本地 TTY 能稳定驱动交互，可补充手工验证：

```bash
poetry run forge
```

手工路径：

1. 在未信任目录启动后选择“不信任”，确认进程直接退出且不创建项目记录。

2. 再次启动后选择“信任”，确认在 Forge home 下创建 `projects/<project-id>/forge.toml` 并进入 REPL。

3. 确认当前项目根目录下没有自动创建 `.forge/`。

4. 再次在同一项目启动，确认不再询问信任。

5. 在该项目子目录启动，确认仍绑定原项目且不再询问信任。

6. 输入 `/status`，确认 `cwd:` 展示工作区列表。

7. 输入 `/add-dir`，通过输入框添加一个存在目录。

8. 再次输入 `/status`，确认 `cwd:` 包含新增目录。

   
