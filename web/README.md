# Forge Web 控制面

`forge` 默认启动的本地 Web 界面 (ADR-0025)。React 19 + TypeScript + Vite +
Ant Design, 没有状态库, 没有路由库。

## 目录怎么分

```
src/
  main.tsx             入口
  App.tsx              外壳: 调各功能的 hook, 接事件流, 摆布局。只做装配
  app/                 外壳周边的布局分块与接线 (含 UiProvider: 语言/主题/全局提示)
  features/<name>/     一个功能的全部: 组件 + hooks + 只它自己用的逻辑
  shared/
    api/               请求客户端 (CSRF, 错误解包, 在途请求记账)
    ui/                跨功能组件 (Markdown / CopyButton)
    format.ts          时长 / token / 路径的显示口径
    lib/               不含 React 的纯逻辑
  types/               后端响应的形状, 只放类型不放函数
  styles/index.css     样式层: 只补 antd 管不到的那几处, 颜色读 --forge-* 变量
```

**一句话规则: 有 React 就进 `app/` 或 `features/`, 没有就进 `shared/lib/`,
只是形状就进 `types/`。**

跨目录一律用 `@/` 别名 (`@/shared/lib/run/turn`), 同目录用 `./x`。全仓没有 `../` 开头的
import —— 见到一个就说明位置放错了。

## 几条约定

- **能用 antd 的组件就别自己画。** 列表、折叠、表格、抽屉、气泡确认、通知、分栏
  全部用 antd 的对应组件。它们自带键盘、焦点、无障碍属性与明暗两套配色 —— 自己糊一个
  `<div className="card">` 只是把这些悄悄丢掉。antd 6 已经废弃 `List`,
  新代码用 `Table` / `Menu` / `Listy`。
- **样式层只补 antd 管不到的地方。** `styles/index.css` 里只有五块: Markdown 正文排版、
  工具活动那一行、输入框、用量胶囊、顶部进度条 —— 它们没有对应的 antd 组件。
  别往里加组件已经能表达的东西 (间距、按钮态、卡片), 那种规则会在升级 antd 时安静失效。
- **颜色只有一处出处: 主题 token。** 配色写在 `app/UiProvider.tsx` (深色 `#0d0f13` 底 +
  `#62d6ad` 绿), 组件用 `theme.useToken()` 取, 样式表用它写出的 `--forge-*` 变量取。
  明暗按 `<html data-theme>` 与系统偏好切换。再抄一份色值不会报错, 只会在换主题时分叉。
- **界面不自己记 loading。** 在途请求由 `shared/api/client.ts` 统一记账,
  组件通过 `useRequestActivity()` 订阅。三十多个调用点各记一个标志的话, 漏掉的那几个
  不会报错, 只会让界面在某些操作上看起来卡住。
- **界面不重新推导后端已经算过的结论。** 审批卡的危险程度、工具的中文名、配置项的
  默认值与生效时机, 全部来自后端字段。前端另写一份不会报错, 只会悄悄漂掉。
- **纯逻辑不写进组件。** `shared/lib/` 与 `features/*/interaction.ts` 这类文件不 import
  react —— 它们是"同样输入得到同样输出"的那部分, 单独放才看得清。
- **注释记的是故障, 不是语法。** 文件里那些讲"原先怎么样、于是出了什么事"的段落,
  每一条都对应一次真实的问题 (跨会话事件串台、流式正文闪一下又消失、空模型节点挡住
  工具合并)。改到那一段时先读它。

## 命令

```bash
npm run dev          # 开发服务器, /api 代理到 127.0.0.1:8765
npm run typecheck    # tsc --noEmit
npm run lint         # eslint
npm run format       # prettier --write
npm run build        # 构建到 ../src/forgecli/interfaces/web/static/
```

仓库根的 `make ci` 会跑 `web-lint` / `web-type` / `web-build` 三道。

前端要连一个跑着的后端: 在仓库根 `make run`, 用它打印的启动链接打开页面
(链接里带一次性 token, 换来一个跨重启复用的会话 cookie)。走 `npm run dev` 时写操作
会被后端的同源检查挡掉 (403) —— 要点按钮就直接用后端那个地址开页面。
