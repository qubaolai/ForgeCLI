# 模型可读正文

Forge 撰写的, 会进入模型上下文的**全部**文字都在这个目录里 (ADR-0031, ADR-0039).
渲染入口只有一个: `application/prompt/template_renderer.py`.

```text
blocks/<block_id>.md.j2   系统提示词的块正文. 有骨架: 哪几节, 什么条件下出现
headings.md.j2            九个块的标题, 一张表
notices/<group>.md.j2     回合中的引导, 收摊通知, 工具回执, 压缩占位. 一条一个 macro
```

`blocks/` 的文件名与 `headings.md.j2` 的键都是 `PromptBlockId` 的取值, 有用例钉住.
`notices/` 按组存: `render_notice("loop.halt")` 里 `loop` 是文件, `halt` 是那份文件里的
一个 macro.

**为什么按组而不是一条一个文件.** 五十几句话拆成五十几个文件, 找是好找了, 但"这几句彼此
矛盾没有"就再也看不出来了 —— 而那正是 ADR-0031 当初收拢正文要解决的问题. 一组并排放着,
一眼扫得完.

## 改动纪律

改任何一份模板, 都要同时升 `PROMPT_TEXT_VERSION` 并更新 `tests/prompt/test_prompt_text.py`
的指纹. 提示词变了模型行为就会变, 这件事必须是显式的 (ADR-0018 §15.3).

`{# #}` 注释不进指纹: 注释一个字都不会进模型上下文, 让改注释也要升版本, 唯一的后果是
没人再写注释. 每段正文为什么这么措辞, 就写在它上面的 `{# #}` 里.

标点一律半角. 回答契约自己写着这条, 我们要求模型做的事, 自己在同一段对话里得先做到 ——
有用例按文件参数化地查.

## notices 的五组, 各有各的读者

**`loop`** —— 回合中注入 transcript 的引导. 它们不是系统提示词, 是回合进行中追加的
USER / TOOL 消息. 模型读它们的方式与读系统提示词完全一样, 所以同样受上面那条改动纪律
约束 —— 它们原先散在循环里, 不升版本也没有快照.

**`stop`** —— 收摊时的文字. 双读者: 先显示给用户, 同时作为 assistant 文本进入历史, 下一轮
模型会读到它. 因此措辞既要对人说得清, 也不能给模型留下"再试一次"的余地.

**`memory`** —— `memory_write` / `memory_forget` 回给模型的那几句. 状态块的正文在
`blocks/memory_state.md.j2`.

**`context`** —— 压缩, 去重与归档占位. 这些文本会**顶替掉** transcript 里原本的工具结果
正文, 措辞因此比别处更要紧: 模型读到的不再是内容本身, 而是这一行 —— 它得凭这一行判断
"要不要去取回来". 三种状态必须分得开: 混成一句"内容见 xxx"的后果是模型去取一个已经被
回收的 id, 拿回一条找不到的错误, 而它读不出这是清理机制还是自己 id 写错了 —— 后一种理解
会让它反复重试.

**`prompt`** —— 提示词自己要用的两段. 其中 `schema_instruction` 会**追加在
`PromptSnapshot.text` 之后**再发给供应商, 因此供应商实际收到的 system prompt 比
`PromptSnapshot.fingerprint` 覆盖的内容多一段. 当前 `complete_structured` 全库没有调用方
(Agent 主循环走原生 tool calling), 所以这个偏差还没有真实影响; 真要用起来, 得让这一段也
进那个指纹.
