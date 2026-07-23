# 2026-07-29：规则引擎 + 高危 deny + 执行流水线骨架

## 今日目标

按 ADR-0009 §3/§7/§8/§14 落地裁决核心：`deny → ask → allow` 规则引擎、内置高危 deny 清单、
能力门/裁决门、以及固定顺序的执行流水线骨架（deny → 执行 → 越界升级）。本日消费 07-28 解析器
输出，产出 allow/ask/deny 裁决。

## 开发指导

- 规则引擎：按 `deny → ask → allow` 优先级求值，第一个匹配即决定；宽 deny 盖过更具体 allow；
  都不匹配落模式默认（决策 6）。三类规则来源：内置高危 deny、内置只读/文件操作 allow、模式预设。
- 内置高危 deny 清单：命令黑名单（`rm -rf` 危险目标、`mkfs`/`dd of=/dev/*`、fork bomb、
  `curl|sh`、`chmod/chown -R` 系统路径、`shred`/`wipefs`）+ 目录/文件黑名单（系统路径、
  `.git/hooks`、`.npmrc` 等可执行配置、`~/.ssh` 等凭证、`.forge/` 自身）。命中转 observation +
  `policy_denied` 事件；配置只可追加不可删。
- 能力门：`plan` 下写/执行工具不进 `tool_catalog`（先于裁决门）。
- 执行流水线骨架（顺序固定不可颠倒）：规范化 → 引擎裁决 → 执行 → 越界升级。本日执行是本地直接
  执行（沙箱留切片 2），但流水线的 deny-先于-执行 顺序现在就钉死。

## 非目标

- 不接沙箱（切片 2）；执行走本地。
- 不实现 ApprovalService 的 once/always 持久化（留 08-03），本日 ask 先走一次性确认桩。
- 不实现 glob 配置语法（留 08-04）。

## 最终产物

- 规则引擎（deny→ask→allow）+ 内置高危 deny 清单 + 能力门/裁决门。
- 执行流水线骨架。
- 裁决与高危拦截的单元测试（含复合/替换绕过用例、`full_access` 也拒高危）。

## 验收重点

- `deny → ask → allow` 顺序正确，宽 deny 盖过具体 allow。
- 高危 deny 在进入执行前拦下（`rm -rf ./*`、写 `.git/hooks`），四模式含 `full_access` 均拒。
- `plan` 下写/执行工具不出现在 `tool_catalog`。
- 执行流水线 deny 检查先于执行，顺序不可颠倒有测试。

## 验收命令

```bash
make ci
```
