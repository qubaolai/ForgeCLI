# 2026-07-31：Shell AST、复合命令和脚本能力提取

## 今日目标

按 ADR-0013 完成原始 Shell 的解析和规范化，把复合命令转换为可审计的 AST / `CommandPlan`，并提取
路径、网络、子进程、动态执行和 `EXECUTE_SCRIPT` 等能力事实。

## 今日范围

- 支持 `&&`、`||`、`;`、换行、`|` 和 `|&`。
- 支持输入、输出、追加重定向、heredoc 和 here-string。
- 扫描命令替换、子 Shell、进程替换、`sh -c`、`bash -c`、`eval`、`xargs`、`find -exec`。
- 识别 `python3 test.py`、`pytest`、`npm test`、`bash verify.sh` 和 Python heredoc 为 `EXECUTE_SCRIPT`。
- 规范化 cwd、home、相对路径和工作区边界。
- 解析失败或不支持语法返回 `PARSE_ERROR` / `UNSUPPORTED`，不自动放行。

## 非目标

- 不在 Parser 内执行命令。
- 不实现所有 Shell 方言；未支持语法必须安全降级为 ASK。
- 不按测试框架穷举白名单。

## 最终产物

- Shell AST / `CommandPlan` 数据结构。
- 命令单元和能力事实提取器。
- 复合命令整体预检接口。
- 绕过语料测试：命令替换、包装器、重定向、heredoc、路径逃逸和动态执行。

## 验收标准

- `cat a.txt | grep b && rm -rf /` 在任何子进程启动前整体拒绝。
- `python3 - <<'PY'` 能提取 heredoc 脚本内容并标记 `EXECUTE_SCRIPT`。
- Shell Parser 不把分析单元错误地拆成多次执行。
- 解析失败默认进入 ASK，不进入 ALLOW。
- 运行 Parser 单测并执行 `make ci`。

## 关联决策

- ADR-0013 §3–§7。
