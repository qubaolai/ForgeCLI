# Forge Web 控制面

`forge` 默认启动的本地 Web 界面 (ADR-0025)。React 19 + TypeScript + Vite,
没有状态库、没有路由库、没有 UI 框架。

## 目录怎么分

```
src/
  main.tsx             入口
  App.tsx              外壳: 调各功能的 hook, 接事件流, 摆布局。只做装配
  app/                 外壳周边的布局分块与接线
  features/<name>/     一个功能的全部: 组件 + hooks + 只它自己用的逻辑
  shared/
    api/               请求客户端 (CSRF, 错误解包, 在途请求记账)
    ui/                跨功能组件 (icons / Markdown / CopyButton)
    hooks/             跨功能 hooks (useEscape / useOutsideClick)
    format.ts          时长 / token / 路径的显示口径
    lib/               不含 React 的纯逻辑
  types/               后端响应的形状, 只放类型不放函数
  styles/              样式, 按功能分块
```

**一句话规则: 有 React 就进 `app/` 或 `features/`, 没有就进 `shared/lib/`,
只是形状就进 `types/`。**

跨目录一律用 `@/` 别名 (`@/shared/lib/run/turn`), 同目录用 `./x`。全仓没有 `../` 开头的
import —— 见到一个就说明位置放错了。

## 几条约定

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
- **样式顺序不能重排。** 类名是全局的, `styles/index.css` 里那串 @import 的次序就是
  原来单文件里的次序。换顺序不会报错, 只会让某个遮罩层换个样子。

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
(链接里带一次性 token, 换来一个跨重启复用的会话 cookie)。
