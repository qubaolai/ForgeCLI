# Agent 工作区持续感知方案

## 1. 目标

目标不是完整记录工作区发生过的所有文件系统事件，而是：

> 让 LLM 像一个真正坐在 IDE 前工作的开发者一样，持续知道“当前工作区”和“自己上一次看到的工作区”相比发生了什么有意义的变化。

需要满足：

- Agent 长时间运行时，可以感知两次 LLM 调用之间的外部文件修改。
- 用户、IDE、其他进程、其他 Agent 修改文件后，LLM 在下一次依赖工作区之前能够感知。
- LLM 自己通过 `write_file`、`apply_patch`、`shell`、Git 等工具造成的变化也能够被观察。
- 一个文件修改后又恢复原样，最终不认为发生有效变化。
- 不保存无限增长的 watchdog event history。
- 不需要每次变化都实时计算 diff。
- 大型代码仓库、深层目录、几十万文件时仍保持较低开销。
- Git 仓库充分利用 Git 已有的状态管理和 diff 能力。
- 普通目录仍然能够使用 filesystem metadata/hash 作为 fallback。
- 防止 LLM 基于已经过期的文件内容继续修改文件。

------

# 2. 核心模型

整个系统区分三个不同的状态：

```text
Git Baseline
    │
    │ HEAD / index
    │
    ▼
Workspace Reality
    │
    │ 当前磁盘真实状态
    │
    ▼
LLM Observation
```

三者含义不同。

## Git Baseline

Git 仓库自身定义的版本状态，例如：

```text
HEAD
index
```

它回答：

> 当前工作区相对于 Git 基线发生了什么变化？

------

## Workspace Reality

磁盘当前真实存在的文件状态。

它可能受到：

```text
LLM
用户
IDE
formatter
build
test
codegen
Git
其他 Agent
其他进程
```

共同影响。

------

## LLM Observation

LLM 上一次真正看到并知道的文件状态。

它回答：

> LLM 当前认为这些文件是什么样子？

Agent 最重要的变化不是：

```text
Git Baseline
      ↓
Workspace Reality
```

而是：

```text
LLM Observation
      ↓
Workspace Reality
```

因为只有这个变化代表：

> 从 LLM 上一次观察之后，又发生了什么新的事情。

------

# 3. 总体架构

```text
                    Workspace
                        │
                        ▼
               Filesystem Watcher
                   watchdog
                        │
                        ▼
                Change Accumulator
                        │
                dirty path set
                        │
                        ▼
                 Change Resolver
                 /             \
                /               \
          Git Workspace       Normal Directory
               │                    │
       Git status/diff          stat/hash
                \                  /
                 \                /
                  ▼              ▼
                  Workspace Reality
                         │
                         ▼
                 Observation Layer
                         │
                 LLM Last-Seen State
                         │
                         ▼
                  Semantic Delta
                         │
                         ▼
                        LLM
```

各层职责保持独立。

------

# 4. Filesystem Watcher

watchdog 与 Workspace / Agent Session 生命周期一致。

```text
Workspace 打开
     │
     ▼
启动 watchdog
     │
     │
     │ 长期运行
     │
     ▼
Workspace 关闭
     │
     ▼
停止 watchdog
```

不按照 LLM turn 启停。

否则：

```text
Turn 1 结束
      │
      │ 用户修改文件
      │ IDE 修改文件
      │ 其他 Agent 修改文件
      ▼
Turn 2 开始
```

中间变化可能完全丢失。

因此：

> Watcher 永久在线，LLM Tool Call 是同步点。

------

# 5. Watchdog 的职责

watchdog 不负责回答：

> 文件最终有没有真正变化？

它只回答：

> 哪些路径可能发生过变化？

例如：

```text
modified src/a.py
modified src/a.py
modified src/a.py
created .a.py.tmp
deleted src/a.py
moved .a.py.tmp -> src/a.py
```

不尝试在 watcher 层理解这些事件。

统一转化成 candidate paths：

```text
dirty_paths = {
    src/a.py
}
```

因此 watchdog event 是：

```text
hint
```

而不是：

```text
fact
```

------

# 6. Change Accumulator

不保存 watchdog event history。

只维护有限状态：

```text
dirty_paths

rename_hints

state:
    NORMAL
    UNKNOWN
```

例如同一个文件修改 1000 次：

```text
modified a.py
modified a.py
modified a.py
...
```

最终仍然只是：

```text
dirty_paths = {
    a.py
}
```

因此运行时间不会直接导致状态无限增长。

------

# 7. NORMAL / UNKNOWN

正常情况下：

```text
state = NORMAL

dirty_paths = {
    src/a.py,
    src/b.py
}
```

说明：

> 系统认为 dirty_paths 足以描述可能发生变化的范围。

如果发生：

```text
watchdog queue overflow
watcher crash
watcher restart
事件无法可靠恢复
dirty_paths 数量过大
Git 大规模 checkout
大型代码生成
```

则：

```text
state = UNKNOWN
```

并允许丢弃具体 dirty paths。

UNKNOWN 的含义是：

> 当前无法保证 candidate set 完整，需要在下一次真正需要 workspace 时重新校准。

------

# 8. Dirty Set 上限

避免：

```text
git checkout
npm install
build
codegen
```

一次产生几十万 dirty paths。

设置阈值：

```text
dirty < threshold
        │
        ▼
继续精确维护

dirty >= threshold
        │
        ▼
state = UNKNOWN
清空 dirty set
```

下一次需要 Workspace 状态时：

```text
UNKNOWN
   │
   ▼
Full Reconciliation
   │
   ▼
重新建立状态
   │
   ▼
NORMAL
```

这是系统的主动退化机制。

------

# 9. Change Resolver

Change Resolver 根据 Workspace 类型选择不同实现：

```text
Workspace
   │
   ├── Git Repo
   │      ↓
   │   Git Resolver
   │
   └── Normal Directory
          ↓
       Filesystem Resolver
```

上层 Agent Runtime 不需要关心具体实现。

------

# 10. Git 仓库策略

Git 仓库优先使用 Git，而不是自己重新实现完整目录 diff。

Git 已经高效处理：

```text
tracked files
untracked files
modified
deleted
added
rename
binary files
symlink
file mode
index
HEAD
.gitignore
```

因此 Git Repo 下：

```text
watchdog
    │
    ▼
candidate paths
    │
    ▼
Git status / Git state
    │
    ▼
必要时 Git diff
```

------

# 11. Git Status 与 Git Diff 分层

不要每次直接生成完整 `git diff`。

第一层只判断状态：

```text
modified
added
deleted
untracked
renamed
```

例如：

```text
 M src/a.py
?? tests/test_a.py
 D scripts/old.py
```

这是 cheap path。

只有真正需要内容变化时才进一步：

```text
git diff -- path
```

因此：

```text
Git Status
    │
    ▼
哪些文件变了？
    │
    ▼
LLM 是否需要具体内容？
    │
    ├── No → 结束
    │
    └── Yes
           │
           ▼
        Git Diff
```

详细 diff 是 lazy 的。

------

# 12. Git 不能替代 Observation Layer

假设：

```text
HEAD:
x = 1

用户修改：
x = 2

LLM read:
x = 2

用户再次修改：
x = 3
```

Git diff 表达：

```text
x = 1
   ↓
x = 3
```

但 LLM 真正需要知道：

```text
x = 2
   ↓
x = 3
```

因此：

```text
Git Diff
```

解决的是：

> Git Baseline → Workspace Reality

而 Agent 真正需要的是：

> LLM Observation → Workspace Reality

所以即使是 Git Repo，仍然必须维护 Observation Layer。

------

# 13. LLM Observation Store

不需要保存整个仓库的 LLM snapshot。

只保存：

> LLM 真正观察过的文件。

例如仓库：

```text
100,000 files
```

LLM 实际读取过：

```text
27 files
```

那么 Observation Store 只需要维护这 27 个文件。

例如：

```text
src/parser.py
    last_seen_version = A

src/lexer.py
    last_seen_version = B

pyproject.toml
    last_seen_version = C
```

这使得 Observation Store 的规模取决于：

```text
LLM 实际工作集
```

而不是：

```text
Repository 总文件数
```

------

# 14. File Version

每次 LLM 真正读取一个文件后记录：

```text
last_seen_version[path]
```

version 可以是：

```text
content hash
Git blob-like hash
内部 content version
```

核心要求只是：

```text
相同内容 → 相同 version
不同内容 → 不同 version
```

例如：

```text
LLM read src/foo.py

content:
A

last_seen_version[src/foo.py] = hash(A)
```

之后文件变成：

```text
B
```

即可判断：

```text
LLM 对 foo.py 的认知已经 stale
```

------

# 15. Workspace-Level Awareness 与 File-Level Awareness

系统维护两个层次的认知。

## Workspace-Level

告诉 LLM：

```text
Workspace changed since your last filesystem observation:

Modified:
- src/a.py

Added:
- tests/test_a.py

Deleted:
- scripts/old.py
```

------

## File-Level

告诉 LLM：

```text
src/a.py changed after you last read it.
```

File-Level 对写操作尤其重要。

------

# 16. Synchronization Point

所有可能依赖或改变 Workspace 的 Tool Call 都视为同步点。

包括：

```text
read_file
read_files
search_files
grep

write_file
apply_patch
create_file
delete_file
move_file

shell
terminal

git checkout
git pull
git merge

build
test
formatter
codegen
```

统一模型：

```text
LLM
 │
 ▼
Tool Request
 │
 ▼
Pre-Sync
 │
 ▼
Execute Tool
 │
 ▼
Post-Sync
 │
 ▼
Tool Result
+
Workspace Delta
 │
 ▼
LLM
```

------

# 17. Pre-Sync

LLM 发起 filesystem-related Tool Call 时：

```text
Tool Request
     │
     ▼
检查 Change Accumulator
```

如果：

```text
dirty_paths = empty
state = NORMAL
```

直接继续。

正常情况下不产生额外磁盘扫描。

如果：

```text
dirty_paths != empty
```

则：

```text
dirty paths
    │
    ▼
Change Resolver
    │
    ▼
Current State
    │
    ▼
与 LLM Observation 比较
    │
    ▼
Semantic Delta
```

然后将新变化告诉 LLM。

------

# 18. Read Tool

例如：

```text
LLM:
read_file(src/foo.py)
```

执行协议：

```text
1. Pre-Sync

2. 处理此前 workspace changes

3. 读取 src/foo.py

4. 计算/取得当前 version

5. 更新：

   last_seen_version[src/foo.py]

6. 返回文件内容
```

从这一刻起：

> LLM 已经观察到 foo.py 当前版本。

------

# 19. Write Tool

Write 类操作需要更严格。

例如：

```text
LLM:
write_file(src/foo.py)
```

执行前比较：

```text
LLM last_seen_version
        │
        ▼
Current Version
```

如果：

```text
same
```

可以继续。

如果：

```text
different
```

说明：

> foo.py 在 LLM 上次读取后又被修改了。

此时应该告诉 LLM：

```text
src/foo.py changed externally after you last read it.

Your current view of this file is stale.
```

不要静默覆盖。

LLM 可以选择：

```text
重新读取
重新生成 patch
merge
放弃修改
```

------

# 20. Optimistic Concurrency

整个系统不假设 Agent 独占 Workspace。

而是采用：

```text
Optimistic Concurrency
```

假设：

```text
用户
IDE
Agent
其他 Agent
build process
formatter
Git
```

都可能并发修改文件。

只有在真正写入之前检查：

```text
last_seen_version == current_version
```

从而避免覆盖别人刚刚做出的修改。

------

# 21. Post-Sync

工具执行完成后进行一次轻量同步。

原因是工具实际产生的变化可能超出 LLM 预期。

例如：

```text
shell("pytest")
```

可能生成：

```text
.pytest_cache
coverage
snapshot
temporary files
```

又例如：

```text
shell("npm install")
```

可能修改：

```text
package-lock.json
node_modules/*
```

因此：

```text
Execute Tool
    │
    ▼
等待 filesystem events 稳定
    │
    ▼
读取 dirty paths
    │
    ▼
Reconcile
    │
    ▼
Semantic Delta
```

然后随 Tool Result 一起返回。

------

# 22. Agent 自身修改的 Attribution

watchdog 不负责判断是谁修改了文件。

Agent Runtime 对自己执行的工具天然拥有信息：

```text
operation_id
tool
start_time
end_time
target_paths
```

例如：

```text
operation #123

tool:
apply_patch

target:
src/a.py
```

Watcher 捕获对应变化后，可以关联到该 Operation。

最终来源只需要区分：

```text
agent
external
unknown
```

不需要猜测 external 到底是：

```text
用户
IDE
另一个 Agent
某个具体进程
```

------

# 23. 不忽略 Agent 自己产生的 Watchdog Event

Agent 自己执行工具时，watchdog 仍然正常监听。

不能因为：

```text
source = agent
```

就直接丢弃事件。

因为：

```text
shell
build
test
formatter
Git
codegen
```

可能产生 Agent Runtime 本身不知道的副作用。

例如：

```text
Agent:
npm install
```

最终可能发现：

```text
package-lock.json changed
```

于是告诉 LLM：

```text
Your previous tool changed:
- package-lock.json
```

------

# 24. Debounce

watchdog event 不实时触发 diff。

Watcher 只：

```text
mark dirty
```

例如编辑器一次保存：

```text
create temp
modify temp
delete original
rename temp
modify directory
```

最终只形成：

```text
dirty:
src/a.py
```

在真正 Synchronization Point 到来时，等待短暂 quiet period 或 Tool Call 完成，然后再 reconcile。

因此系统追求：

> 下一次 LLM 需要 Workspace 时状态已经稳定。

而不是：

> 每个 filesystem event 都立即通知 LLM。

------

# 25. 修改后恢复原样

例如：

```text
LLM last seen:

abc
```

用户：

```text
abc
 ↓
abcd
 ↓
abc
```

watchdog 会产生 modified event。

但是 Reconciler 比较：

```text
last_seen hash
vs
current hash
```

发现：

```text
same
```

最终：

```text
unchanged
```

不通知 LLM。

因此系统关心：

```text
最终状态变化
```

而不是：

```text
操作历史
```

------

# 26. Semantic Delta

最终传给 LLM 的不是 watchdog event，而是语义化变化：

```text
WorkspaceDelta

added[]
modified[]
deleted[]
renamed[]

stale_files[]

source:
    agent
    external
    unknown

checkpoint
```

例如：

```text
Workspace changed since your last filesystem observation:

External changes:
- modified: src/parser.py
- added: tests/test_parser.py

Your previous tool changed:
- package-lock.json

Stale files:
- src/parser.py
```

------

# 27. Detailed Diff

默认不生成完整 diff。

默认只告诉：

```text
path
change type
source
```

例如：

```text
Modified externally:
- src/parser.py
```

只有以下情况再生成 diff：

```text
LLM 正准备读取这个文件

LLM 准备修改这个文件

LLM 主动询问具体变化

文件属于当前任务的重要文件

变化较小且值得直接展示
```

即：

```text
change detected
      │
      ▼
semantic summary
      │
      ▼
does LLM need details?
    /             \
   no             yes
   │               │
结束             Diff
```

------

# 28. Git Repo 中的 Detailed Diff

Git Repo 优先让 Git 生成 diff。

因为 Git 对以下情况的处理更成熟：

```text
text
binary
rename
mode
symlink
index
HEAD
```

但需要注意：

Git 默认 diff 的 baseline 不一定等于 LLM Observation。

因此：

```text
HEAD → Current
```

可以直接使用 Git diff。

而：

```text
LLM Last Seen → Current
```

则需要 Observation Store 保存的版本参与比较。

两者用途不同。

------

# 29. 普通目录 Fallback

如果不是 Git Repo：

```text
watchdog
    │
    ▼
dirty paths
    │
    ▼
filesystem metadata
    │
    ▼
必要时 hash
```

判断流程：

```text
文件存在状态
    │
    ├── old missing / current exists
    │       → added
    │
    ├── old exists / current missing
    │       → deleted
    │
    ▼
size
    │
    ├── different
    │       → modified
    │
    ▼
mtime/version hint
    │
    ▼
必要时 hash
    │
    ├── same
    │      → unchanged
    │
    └── different
           → modified
```

普通目录只是 Resolver 不同。

Observation Layer 和 Agent Runtime 完全不变。

------

# 30. Ignore Policy

Watcher 与 Resolver 必须共享同一套 Ignore Policy。

典型忽略：

```text
.git/
node_modules/
.venv/
venv/
__pycache__/
.pytest_cache/
.idea/
.vscode/
dist/
build/
coverage/
tmp/
cache/
```

支持：

```text
.gitignore
Agent ignore config
Workspace config
Tool-specific ignore rules
```

Git Repo 优先尊重 Git ignore semantics。

避免：

```text
npm install
```

导致数万条无价值变化污染系统。

------

# 31. 大规模 Workspace Change

例如：

```text
git checkout another-branch
git pull
代码生成
大型 formatter
```

可能改变数千文件。

此时不要给 LLM 展开：

```text
5000 file changes
```

应该聚合：

```text
Workspace changed substantially since your last observation.

1832 files modified
420 files added
317 files deleted

Previously observed files that changed:
- src/parser.py
- src/model.py
- pyproject.toml
```

LLM 曾经观察过的文件拥有最高优先级。

因为这些文件的变化真正意味着：

> LLM 原有认知失效。

------

# 32. Context 优先级

Workspace Delta 进入 LLM Context 时按照以下优先级：

```text
1. 当前 Tool Call 的目标文件

2. LLM 曾经读取、现在发生变化的文件

3. 当前任务相关文件

4. Agent 上一个 Tool Call 产生的重要变化

5. 其他 Workspace Change
```

大量低价值变化只提供 summary。

------

# 33. Observation Checkpoint

Checkpoint 的语义不是：

```text
watchdog 上一次 flush
```

也不是：

```text
上一轮 conversation
```

而是：

> LLM 上一次真正被告知 Workspace 状态的时刻。

例如：

```text
watchdog:
a.py changed

reconciler:
a.py modified
```

但 LLM 此时没有进行任何 filesystem Tool Call。

那么：

```text
LLM Observation
```

仍然不能推进。

因为 LLM 并不知道变化。

直到：

```text
Pre-Sync
   │
   ▼
告诉 LLM：
a.py changed externally
```

之后才把这个变化标记为：

```text
observed
```

------

# 34. Git 与 Watchdog 的职责

两者不是替代关系。

## Watchdog

回答：

> 有什么东西可能刚刚发生变化？

特点：

```text
incremental
低延迟
低成本
适合长期监听
```

------

## Git

回答：

> 当前 Git Workspace 到底处于什么状态？

特点：

```text
准确
成熟
理解 Git semantics
适合 status / diff / rename / tracked state
```

------

## Observation Store

回答：

> 这些变化里面，哪些是 LLM 还不知道的？

这是整个 Agent 感知系统最重要的一层。

最终关系：

```text
watchdog
    =
Something may have changed

Git / Filesystem Resolver
    =
What is actually true now?

Observation Store
    =
What does the LLM not know yet?
```

------

# 35. 推荐运行流程

```text
Agent / Workspace Start
        │
        ▼
Detect Git Repository
        │
        ▼
Load Ignore Policy
        │
        ▼
Initialize Lightweight State
        │
        ▼
Start Watchdog
        │
        ▼
────────────────────────────────────
          Long Running Session
────────────────────────────────────
        │
        │
        │ User / IDE / Process
        │ modifies files
        │
        ▼
     Watchdog
        │
        ▼
 Change Accumulator
        │
        ▼
   dirty paths
        │
        │
        │
LLM continues reasoning
        │
        ▼
Filesystem-related Tool Request
        │
        ▼
      Pre-Sync
        │
        ├── clean
        │      │
        │      ▼
        │   continue
        │
        └── dirty
               │
               ▼
        Change Resolver
          /          \
        Git        Filesystem
          \          /
           ▼        ▼
        Current Reality
               │
               ▼
        Observation Compare
               │
               ▼
         Semantic Delta
               │
               ▼
        Inform LLM if needed
               │
               ▼
          Execute Tool
               │
               ▼
        Watchdog captures
          side effects
               │
               ▼
           Post-Sync
               │
               ▼
         Tool Result
              +
       Workspace Delta
               │
               ▼
              LLM
```

------

# 36. 最终数据结构

长期状态可以保持非常小：

```text
WorkspaceObserver

repo_type:
    git | filesystem

watcher

dirty_paths

rename_hints

observer_state:
    NORMAL | UNKNOWN

observed_files:
    path -> last_seen_version

workspace_summary_state

active_operation:
    operation_id
    tool
    start_time
    target_paths

ignore_policy
```

不需要：

```text
完整 watchdog history
完整仓库内容 snapshot
每个文件永久 diff history
```

------

# 37. 最终设计原则

1. **Watcher 生命周期跟 Workspace 走，而不是跟 LLM Turn 走。**
2. **Watchdog 永久在线，但不保存无限事件历史。**
3. **Watchdog 只负责发现 candidate paths。**
4. **Git Repo 优先使用 Git 解析 Workspace Reality。**
5. **普通目录使用 filesystem metadata + lazy hash fallback。**
6. **Git status 用于便宜地发现状态，Git diff 按需生成。**
7. **Git Baseline、Workspace Reality、LLM Observation 必须明确区分。**
8. **Agent 最关心的是 `LLM Observation → Workspace Reality`。**
9. **只维护 LLM 真正观察过的文件版本，不维护整个仓库的 LLM snapshot。**
10. **所有依赖或可能改变 Workspace 的 Tool Call 都是 synchronization point。**
11. **Tool Call 前执行 Pre-Sync，让 LLM 不基于过期环境行动。**
12. **Tool Call 后执行 Post-Sync，让 LLM 感知工具真实产生的副作用。**
13. **Write / Patch 前检查目标文件是否自上次读取后发生变化。**
14. **使用 optimistic concurrency 防止 Agent 静默覆盖外部修改。**
15. **Agent 自己产生的文件事件仍然保留，只增加 attribution。**
16. **变化来源只需要区分 agent / external / unknown，不尝试从 watchdog 猜具体进程或用户。**
17. **文件修改后恢复原始内容，最终视为 unchanged。**
18. **默认只生成 Semantic Delta，详细 Diff 按需计算。**
19. **大量变化时优先告诉 LLM 它曾经观察过、现在已经变化的文件。**
20. **dirty paths 过多、watcher overflow 或状态不可信时进入 UNKNOWN，并在下一次需要时 Full Reconcile。**
21. **Ignore Policy 在 Watcher、Git、Filesystem Resolver 之间保持一致。**
22. **LLM Context 中只放对当前认知有意义的变化，不倾倒完整文件系统事件。**
23. **Observation Checkpoint 只有在 LLM 真正收到变化后才能推进。**
24. **Git 负责理解仓库，watchdog 负责持续感知，Observation Store 负责理解 LLM 的认知。**
25. **最终系统不追求记录“过去发生过什么”，而追求在 LLM 每次准备操作工作区时，都能回答三个问题：**

```text
现在工作区是什么状态？

和我上次看到的时候相比发生了什么？

我现在准备操作的文件，还是不是我以为的那个版本？
```