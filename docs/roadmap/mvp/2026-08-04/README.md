# 2026-08-04：glob 规则配置语法 + 出区读写裁决

## 今日目标

按 ADR-0009 §10/§11 实现 glob 规则配置语法（Bash 模式 + gitignore 式路径锚定）与出工作区
读/写的裁决，并支持种子 allow 探测。本日让用户能用 `Read(~/.ssh/**)`、`Bash(npm run *)` 一类
规则精确追加 allow/ask/deny。

## 开发指导

- Bash 模式：`Bash(npm run *)` 前缀、`Bash(git * main)` 跨参数、`:*` 尾部通配、空格前 `*` 强制
  词边界（`Bash(ls *)` 不匹配 `lsof`）。匹配前先经 07-28 规范化，须匹配每个子命令。
- 文件路径模式（gitignore 语义，`*` 单段 / `**` 跨目录）：`//path`（fs 根）、`~/path`（主目录）、
  `/path`（设置源相对，非 fs 根）、`path`/`./path`（cwd 相对）；裸文件名任意深度匹配；symlink
  检查链接与目标两条路径。
- 规则进 `deny → ask → allow` 引擎，deny 恒压过 allow；`always` 学习授权落成此语法的 allow。
- 出区读裁决：区外读（含 Grep/Glob/`@file`）默认 ask，防自由读 `~/.ssh/id_rsa` 喂进上下文；
  可由 `Read(...)` allow 精确放宽。
- 种子 allow：从 `pyproject.toml` / `package.json` / `Makefile` 探测 test/lint/build 脚本预填
  allow（预填项，不是闸门）。

## 非目标

- 不实现 WebFetch 域规则 / PowerShell / 托管设置（无对应工具，按同语法留待引入）。
- 不接沙箱。

## 最终产物

- glob 规则匹配器（Bash 模式 + gitignore 路径锚定）+ 出区读写裁决 + 种子 allow 探测。
- 规则语法与路径锚定的单元测试（`//`/`~/`/`/`/`./`、`*` vs `**`、词边界、symlink 双路径）。

## 验收重点

- `Bash(npm run *)` allow、`Read(~/.ssh/**)` deny、`Read(src/**)` allow 按语义匹配；
  `Bash(ls *)` 不匹配 `lsof`。
- 出工作区读默认 ask，可由 `Read(...)` allow 放宽；区内读免提示。
- `always` 授权落成 glob allow 规则并复用同一匹配器。

## 验收命令

```bash
make ci
```
