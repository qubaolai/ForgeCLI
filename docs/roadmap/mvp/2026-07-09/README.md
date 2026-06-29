# 2026-07-09：/config 当前模型、用途档位与档位候选编辑

## 今日目标

补齐 ADR-0011 §19 验收要求的 `/config` 配置能力：当前模型、用途档位、档位候选模型、优先级、selection 和 thinking 默认值。让 07-01~07-02 已经落地的目录 / 选择 / 路由结构可以从 REPL 内被查看与编辑。

## 开发指导

- `/config` 存储源采用「档位 -> 候选模型列表」，与 ADR-0011 §16 / §5 对齐，不把「档位」作为模型唯一属性写入模型目录。
- 当前模型配置：
  - 查看与设置 `[model].provider` / `[model].model`（主 Agent 模型）。
  - 模型详情页提供「设为当前模型」快捷操作。
- 用途档位配置：
  - 查看与设置 `[model].*_tier`（title / summary / compact / planning / review / structured）。
- 档位候选配置（档位视角）：
  - 编辑某档位（fast / smart / default）的候选模型、priority、selection、fallback。
  - 编辑候选级 thinking 默认值。
- 模型视角快捷入口：
  - 把某个模型加入 fast / smart / default，仅作为写入档位候选列表的快捷方式。
- 配置写入复用既有 `application/config` / `application/llm/config` 的 store port 和 TOML 适配器；当前模型 / 档位为项目级，provider / 凭证为应用级（ADR-0008）。
- 校验：写入前经 `ModelCatalogService` 做存在性与能力校验；明文 key 不进入 TOML，provider adapter 必须由代码注册。

## 非目标

- 不实现完整可观测性面板（`/status` 诊断聚合留在后续）。
- 不实现企业 allowlist 编辑 UI（保留配置位）。
- 不改动 provider 注册表的代码级封闭性。

## 最终产物

- `/config` 当前模型 / 用途档位 / 档位候选 / priority / selection / thinking 默认值编辑闭环。
- 档位视角与模型视角两类入口。
- 配置读写单元测试与 catalog 校验测试。

## 验收重点

- `/config` 可配置当前模型、用途档位、档位候选模型、优先级、selection 和 thinking 默认值（ADR-0011 §19）。
- 当前模型与档位语义分离：当前模型不被写成档位属性。
- 写入经 catalog 校验，未知 provider / 不存在模型 / 能力不足被拒绝。
- 明文 key 不进入 TOML、事件或日志。

## 验收命令

```bash
make ci
printf '/config\nexit\n' | env FORGE_CONFIG_DIR="$(mktemp -d)" poetry run forge
```

如果 TTY 限制导致管道无法完整驱动 REPL，应以 `/config` 用例与配置 store 集成测试作为主验收。
