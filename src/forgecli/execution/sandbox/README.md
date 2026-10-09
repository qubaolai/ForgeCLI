# 沙箱配置项说明

本文档说明 `SandboxSettings` 中的所有配置项、默认值、含义，以及推荐的配置方式。

---

## 一、配置项总览

| 配置项 | 类型 | 默认值 | 作用域 |
|---|---|---|---|
| `enabled` | bool | `True` | 总开关 |
| `auto_allow_bash_if_sandboxed` | bool | `True` | 权限判定 |
| `excluded_commands` | tuple[str, ...] | `()` | 权限判定 |
| `allow_unsandboxed_commands` | bool | `True` | 权限判定 |
| `allowed_domains` | tuple[str, ...] | `()` | 网络 |
| `denied_domains` | tuple[str, ...] | `()` | 网络 |
| `allow_unix_sockets` | tuple[str, ...] | `()` | 网络 |
| `allow_all_unix_sockets` | bool | `False` | 网络 |
| `allow_local_binding` | bool | `False` | 网络 |
| `allow_write` | tuple[str, ...] | `()` | 文件系统 |
| `deny_write` | tuple[str, ...] | `()` | 文件系统 |
| `deny_read` | tuple[str, ...] | `()` | 文件系统 |
| `allow_read` | tuple[str, ...] | `()` | 文件系统 |


---

## 二、配置项详解

### 1. 总开关

#### `enabled`

- **类型**：`bool`
- **默认值**：`True`
- **含义**：是否启用沙箱。
- **说明**：`enabled=True` 且 `confined=True` 时，沙箱才真正生效（见 `active` 属性）。

---

### 2. 权限判定

#### `auto_allow_bash_if_sandboxed`

- **类型**：`bool`
- **默认值**：`True`
- **含义**：围栏立起来时，Bash 命令自动放行。
- **说明**：仍会先检查 `deny` / `ask` 规则。目的是减少已在沙箱内时的重复确认。

#### `excluded_commands`

- **类型**：`tuple[str, ...]`
- **默认值**：`()`
- **含义**：含这些前缀的命令**不进围栏**（例如 `docker *`）。
- **说明**：⚠️ **这不是安全边界**，仅用于兼容无法在沙箱内运行的命令。默认空表示所有命令都进围栏。

#### `allow_unsandboxed_commands`

- **类型**：`bool`
- **默认值**：`True`
- **含义**：模型能否用 `dangerously_disable_sandbox` 跳出围栏。
- **说明**：⚠️ **高危逃生舱**。名字带 `dangerously_` 前缀。**建议改为 `False`**，除非明确需要模型在沙箱误报时自救。

---

### 3. 网络配置

#### `allowed_domains`

- **类型**：`tuple[str, ...]`
- **默认值**：`()`
- **含义**：只有这些域名能经代理出去。
- **取值语义**：
  - `()`（空）：**全拦**（不认识的域名会问一次）。
  - `"*"`：**全部放行**，不经代理，围栏直接放开网络。
  - `"*.example.com"`：匹配 example.com 的**严格子域**。
  - `"example.com"`：匹配该域名本身。

#### `denied_domains`

- **类型**：`tuple[str, ...]`
- **默认值**：`()`
- **含义**：禁止访问的域名（优先于 `allowed_domains`）。

#### `allow_unix_sockets`

- **类型**：`tuple[str, ...]`
- **默认值**：`()`
- **含义**：允许连接 / 监听的 unix socket 路径（`subpath` 匹配）。
- **说明**：**仅 macOS 有效**。Linux 的 seccomp 无法按路径控制。

#### `allow_all_unix_sockets`

- **类型**：`bool`
- **默认值**：`False`
- **含义**：允许所有 unix socket。
- **说明**：⚠️ 放开范围大，默认关闭。仅在确实需要时开启。

#### `allow_local_binding`

- **类型**：`bool`
- **默认值**：`False`
- **含义**：允许绑定本地端口与连接本机（dev server 一类）。
- **说明**：默认关闭，需要跑本地服务时开启。

---

### 4. 文件系统配置

#### `allow_write`

- **类型**：`tuple[str, ...]`
- **默认值**：`()`
- **含义**：允许写入的路径（`subpath` 匹配）。
- **说明**：默认无可写区。配合上层"默认禁写"策略使用。

#### `deny_write`

- **类型**：`tuple[str, ...]`
- **默认值**：`()`
- **含义**：在可写区内**再挖掉**的路径。
- **说明**：优先于 `allow_write`（后写的规则覆盖先写的）。

#### `deny_read`

- **类型**：`tuple[str, ...]`
- **默认值**：`()`
- **含义**：禁止读取的路径。
- **说明**：⚠️ 默认空，意味着上层"默认允许读"时，敏感路径（如 `~/.ssh`）默认可读。**建议显式配置敏感路径**。

#### `allow_read`

- **类型**：`tuple[str, ...]`
- **默认值**：`()`
- **含义**：在 `deny_read` 区域内**再放开**的路径。
- **说明**：**优先于 `deny_read`**。用于在禁读区中精确放行个别子路径。

---

### 5. 运行时状态（勿手动配置）

#### `confined`

- **类型**：`bool`
- **默认值**：`False`
- **含义**：围栏实际是否立起来了。
- **说明**：由**启动期 Provider 自测**写入，**不是用户配置**。`confined=False` 等价于 `enabled=False`，自动放行不生效。

---

## 三、配置优先级

配置来源分三层，优先级从高到低：

1. **项目级配置**（`project_settings_file(workspace_id)`）
2. **用户级配置**（`user_settings_file()`）
3. **默认值**（`SandboxSettings()`）

> ⚠️ **重要**：项目级配置**一旦存在就整体接管**（替换），**不是字段级合并**。项目级缺失的字段会走默认值，**不会回退到用户级**。

`confined` 字段例外：它始终由**运行时自检结果**决定，不受配置文件影响。

---

## 四、示例配置

### 1. 保守 / 高安全场景（推荐默认）

```yaml
sandbox:
  enabled: true
  # 不允许模型跳出围栏
  allow_unsandboxed_commands: false
  # 不预设任何例外命令
  excluded_commands: []

  network:
    # 空 = 全拦网络
    allowed_domains: []
    denied_domains: []
    allow_local_binding: false
    allow_all_unix_sockets: false
    allow_unix_sockets: []

  filesystem:
    # 只放行必要的工作目录
    allow_write:
      - "/path/to/project"
    # 显式保护敏感路径
    deny_read:
      - "~/.ssh"
      - "~/.aws"
      - "~/.config/gh"
```

### 2. 开发场景（需要网络与本地服务）

```yaml
sandbox:
  enabled: true
  allow_unsandboxed_commands: false

  network:
    # 只放行必要域名
    allowed_domains:
      - "github.com"
      - "*.githubusercontent.com"
      - "registry.npmjs.org"
    # 允许本地 dev server
    allow_local_binding: true

  filesystem:
    allow_write:
      - "/path/to/project"
      - "/tmp"
    deny_write:
      - "/path/to/project/.git"
      - "/path/to/project/.env"
    deny_read:
      - "~/.ssh"
      - "~/.aws"
```

### 3. 宽松场景（仅当明确知情时）

```yaml
sandbox:
  enabled: true
  # 明确允许跳出围栏
  allow_unsandboxed_commands: true

  network:
    # 网络全开，绕过代理
    allowed_domains:
      - "*"

  filesystem:
    allow_write:
      - "/"
```

> ⚠️ 宽松配置会显著降低安全性，请仅在受控环境下使用。

---

## 五、配置要点总结

1. **默认以安全为先**：`enabled=True`、网络默认全拦、无可写区、本地绑定默认关。
2. **`allow_unsandboxed_commands` 建议设为 `False`**：默认 `True` 让模型拥有跳出围栏的能力，与安全取向冲突。
3. **务必显式配置 `deny_read`**：默认空 + 上层默认允许读，会让敏感文件可读。
4. **`excluded_commands` 不是安全边界**：只是便利后门，慎用。
5. **网络 `"*"` 表示完全放开**：会绕过代理，谨慎使用。
6. **项目级配置整体替换用户级**：项目级要自包含，不能指望继承用户级的字段。
7. **`confined` 不要手动配**：它由自测决定，配置无效。
8. **路径用 `subpath` 语义**：父目录放行会覆盖整个子树。
9. **`allow_read` 优先于 `deny_read`**、**`deny_write` 优先于 `allow_write`**：利用"后写的规则赢"来精修边界。

---

## 六、最小可用配置示例

```yaml
sandbox:
  enabled: true
  allow_unsandboxed_commands: false
  filesystem:
    allow_write:
      - "/path/to/project"
    deny_read:
      - "~/.ssh"
      - "~/.aws"
```

这条配置的含义：

- 启用沙箱；
- 不允许模型跳出围栏；
- 只允许写项目目录；
- 保护 SSH / AWS 凭据不被读取；
- 网络默认全拦（未配置 `allowed_domains`）。