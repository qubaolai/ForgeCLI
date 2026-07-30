# 2026-08-03：规则决策、四种 mode 与脚本风险评估

## 今日目标

完成 `allow / deny / ask` 决策、Hard Deny、四种 mode 的能力预算，并接入无沙箱 auto 所需的
Background Safety Classifier 接口和脚本分析缓存。

## 今日范围

- 实现规则优先级：`Hard Deny > Ask > Allow`。
- 实现语义 Allow，不允许仅按可执行文件名放行。
- 实现 `plan`、`accept_edits`、`auto`、`full_access` 的能力矩阵。
- 实现 `EXECUTE_SCRIPT` 的环境感知策略：强沙箱 auto、无沙箱 auto、accept_edits。
- 定义分类器结构化输入、输出、置信度和 fail-safe 行为。
- 使用脚本内容、配置、依赖、策略和执行环境哈希缓存风险分析。
- 产生 `policy_denied`、`approval_required` 和分类结果审计事件。

## 非目标

- 不让 LLM 覆盖 Hard Deny。
- 不让分类器直接调用 ShellTool 或修改权限策略。
- 不实现真实跨平台沙箱；沙箱生命周期在 8/4 完成。

## 最终产物

- `CommandPolicy`、`Decision`、mode capability 和 Hard Deny 规则。
- `SafetyClassifier` port、fake classifier 和结构化结果校验。
- 通过现有 LLM Gateway 调用分类器的最小 adapter；默认测试使用 fake classifier。
- 脚本风险缓存及失效规则。
- 四种 mode × 脚本类型的单元测试。

## 验收标准

- accept_edits 下脚本执行默认 ASK。
- 强沙箱 auto 可在执行环境允许时自动执行脚本。
- 无沙箱 auto 对新脚本、变更脚本或未知脚本调用分类器。
- 分类器超时、异常、非法输出或低置信度进入 ASK。
- Hard Deny 在所有 mode 下均不可覆盖。
- 运行策略单测并执行 `make ci`。

## 关联决策

- ADR-0013 §4、§8–§11。
