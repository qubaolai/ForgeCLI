# 2026-06-30：配置作用域收敛与交互式配置复用

## 今日目标

在保留当前用户级配置实现的基础上，引入项目级配置源，让配置系统从“单文件用户配置”演进为“可合并配置视图”。入口仍然保持单一：用户通过裸 `forge` 进入 REPL，再使用 `/config` 完成配置查看和修改。

## 当前基线

当前配置实现合理但偏 MVP 简化：

- 用户级配置目录：`~/.forge` 或 `FORGE_CONFIG_DIR`。
- `ConfigService` 当前只接一个 store。
- `/config` 是主要交互入口。
- `[model]` 运行时默认选择已在 25 日加入。
- Typer 层不提供 `forge config ...` 业务子命令。

## 开发指导

- 保留用户级配置作为默认写入目标。
- 增加项目级 `.forge/config.toml` source，但不要求自动初始化。
- `ConfigService` 支持多个只读/可写 source 的合并。
- 优先级采用当前阶段可实现版本：
  1. 交互式命令输入产生的临时覆盖
  2. 环境变量
  3. 项目 `.forge/config.toml`
  4. 用户 `~/.forge/config.toml`
  5. 默认值
- `/config` 默认写入用户级配置；项目级写入通过明确的 service 选项或后续菜单项预留。
- `/config` 继续复用同一 `ConfigService`，不复制校验逻辑。
- 不新增 `forge config list/get/set/validate`。

## 非目标

- 不实现企业只读策略。
- 不实现 config migrate。
- 不实现完整 env var 映射表之外的动态配置。
- 不写入凭证值。
- 不新增脚本式 Typer 业务入口。

## 最终产物

- 多 source `ConfigService`。
- 用户级和项目级配置 source。
- `/config` 继续可用，并复用新的合并配置视图。
- 配置作用域测试，确保不污染真实 HOME。

## 验收命令

```bash
make ci
printf '/config\nexit\n' | env FORGE_CONFIG_DIR="$(mktemp -d)" poetry run forge
```

如果菜单交互受 TTY 限制，应以 `ConfigService`、store 和 `/config` handler 的单元/集成测试作为主验收。
