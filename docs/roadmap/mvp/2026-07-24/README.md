# 2026-07-24：Git 与 Test 工具

## 今日目标

实现 git 状态、diff、show 和测试命令工具。

## 开发指导

- `git.status`、`git.diff`、`git.show` 默认只读。
- `test.run` 从配置读取默认测试命令。
- 测试输出较长时写 artifact。

## 最终产物

- git 工具。
- test 工具。
- 工具事件写入测试。

## 代码验收

我会检查 git 工具是否不做提交、push、reset，测试工具是否能处理失败输出。

