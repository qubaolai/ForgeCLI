# 2026-07-01：模型目录、ModelSelection 与用途档位

## 今日目标

把当前模型、显式模型和用途档位三类选择落成可测试的解析规则，并让模型目录成为单个模型能力的唯一事实来源。

## 开发指导

- 定义或收敛 `ModelCatalogService` 的运行时只读视图：
  - 内置目录为基线。
  - 用户配置的 provider/model 元数据按 provider + model 合并覆盖。
  - 能力、上下文窗口、价格、deprecation 和 allowlist 都从 catalog 读取。
- 定义 `ModelSelectionResolver`：
  - `current_model` 读取当前项目或 session 主 Agent 模型。
  - `explicit_model` 校验 provider/model 存在，默认不 fallback。
  - `tier` 解析用途档位，交给 router 选择候选模型。
- 定义用途 origin 到默认选择的映射：
  - `chat`、`act`、`final_summary` 默认 `current_model`。
  - `title`、`summary`、`compact` 默认 `tier: fast`。
  - `plan`、`review` 默认 `tier: smart`。
  - `structured_classification` 默认 `tier: fast`。
- 明确当前模型不属于档位；档位是系统用途模型池。
- 更新配置模型，使 `[model]` 和 `[model_tiers]` 的语义与 ADR-0011 对齐。

## 非目标

- 不实现候选排序和 fallback 执行。
- 不实现 `/config` 完整交互菜单。
- 不接真实 provider。

## 最终产物

- 模型目录运行时视图。
- `ModelSelectionResolver`。
- origin 到 selection 的默认映射。
- 配置结构与 ADR-0011 的最小对齐。
- 单元测试覆盖 current、explicit、tier 三类选择。

## 验收重点

- `ProviderCapabilities` 不重复声明单模型能力。
- `current_model` 不做模型 fallback。
- `explicit_model` 没有显式允许时不跨 provider/model fallback。
- tier 选择不直接读取 TOML，必须通过 catalog 和 tier config 的 application API。

## 验收命令

```bash
make ci
poetry run pytest tests
```
