# 2026-09-05：结构化询问与即时问题卡片

## 目标

按已确认的原型改进 `ask_user`：每次工具调用只提出一个问题，后端决定单选或多选，
选项显示标题和说明，至多一个推荐项，只标注“推荐”。保留自由文本与“跳过本题”，
没有“全部不回答”。

## 实现

- `ask_user` v2 使用原生结构化工具参数，Schema 和领域校验在等待之前完成。
  `question` 必填；`description` 可选；`selection_mode` 为 `single` 或 `multiple`
  （兼容旧调用，省略时默认 `single`）；选项为 `value / label / detail`，三者必填且
  非空；`recommended_option_id` 可选，只能引用一个已有 value。
- 工具结果与回答审计包含 `status: answered | skipped`、`selected_values`、`text`。
  选择和补充文字同时保留；跳过不代表选择推荐项，也不产生执行授权。
- Web 按后端声明使用 radio 或 checkbox，不自动选中推荐项。空白不能提交，提交期间
  禁止重复操作，提交失败保留选择和文字。不同 prompt_id 重置卡片状态。
- 待答队列入队后发布 `prompt_requested`，移除后发布 `prompt_resolved`。
  Web 收到事件立即独立拉取 `/prompts`，不等待工作区其他请求；每次 SSE 连接成功
  同步待答队列，较旧 HTTP 响应不能覆盖较新的问题状态。
- 终端读取同一 payload，编号单选/逗号分隔多选，可补充文字，`/submit` 提交、
  `/skip` 跳过，Ctrl-C 仍停止整轮。
- 在释放等待者前持久化用户回答；记录失败时仍可重试。取消或关闭后不再接受迟到回答。

## 验证命令

- `poetry run pytest tests/tools/test_ask_user.py tests/security/test_human_prompt_channel.py tests/tui/test_prompt_card.py tests/web/test_question_flow.py`
- `make web-test web-build`
- `make lint type arch test`

HTTP 集成用例验证后台工具仍在等待时通知已经发出、待答查询可用、多选或跳过可以
结束等待、重复提交被拒绝。前端渲染测试覆盖单选/多选互斥、唯一推荐、说明展示、
纯文本转义与默认未选中。


## 2026-09-06 结构修正

统一人机交互按功能包组织：`domain/human_interaction/prompt.py`、
`application/human_interaction/service.py`、`interfaces/runtime/human_interaction/broker.py`。
所有生产代码、测试和 ADR 引用同步迁移，旧根目录文件移除，无兼容转发层。
架构边界检查扩大到整个功能包，保留原有单选、多选、跳过、推荐与即时通知语义。
