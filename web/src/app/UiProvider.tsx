/** 全局 UI 环境: 中文文案, 明暗主题, 以及 message / notification / modal 的实例。
 *
 * 主题跟着两个来源走: 设置项写在 <html data-theme> 上, 没有设置时跟随系统。
 *
 * 配色是 Forge 自己的那一套 (深色 #0d0f13 底 + #62d6ad 绿), 不用 antd 的出厂色 ——
 * 它按 token 写在这里一份, 组件与 styles/index.css 都从这里取, 不各存一份。
 */

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { App, ConfigProvider, theme } from "antd";
import zhCN from "antd/locale/zh_CN";

const DARK = {
  colorPrimary: "#62d6ad",
  colorBgLayout: "#13161c",
  colorBgContainer: "#13161c",
  colorBgElevated: "#191d25",
  colorBorder: "#282e39",
  colorBorderSecondary: "#232833",
  colorText: "#e7e9ee",
  colorTextSecondary: "#d5d9e2",
  colorTextTertiary: "#9198a7",
  colorTextQuaternary: "#767d8b",
  colorWarning: "#f4bd61",
  colorError: "#ef7a81",
  colorSuccess: "#62d6ad",
  colorFillSecondary: "#202530",
};

const LIGHT = {
  colorPrimary: "#168665",
  colorBgLayout: "#ffffff",
  colorBgContainer: "#ffffff",
  colorBgElevated: "#ffffff",
  colorBorder: "#e0e4ea",
  colorBorderSecondary: "#eceff4",
  colorText: "#1e232c",
  colorTextSecondary: "#333a45",
  colorTextTertiary: "#6f7683",
  colorTextQuaternary: "#8b93a1",
  colorWarning: "#b7791f",
  colorError: "#c0413f",
  colorSuccess: "#168665",
  colorFillSecondary: "#eef1f5",
};

/** 上面的 token 派生不出来的几个: 输入框与代码块的底、投影。 */
const EXTRA = {
  dark: { raise: "#191d25", codeBg: "#10141a", codeInline: "#232a35", shadow: "#00000055" },
  light: { raise: "#ffffff", codeBg: "#fafbfc", codeInline: "#eceff4", shadow: "#0f172a1a" },
};

function isDarkTheme() {
  const selected = document.documentElement.dataset.theme;
  return selected ? selected === "dark" : !window.matchMedia("(prefers-color-scheme: light)").matches;
}

function useDarkTheme() {
  const [dark, setDark] = useState(isDarkTheme);
  useEffect(() => {
    const update = () => setDark(isDarkTheme());
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const media = window.matchMedia("(prefers-color-scheme: light)");
    media.addEventListener("change", update);
    update();
    return () => {
      observer.disconnect();
      media.removeEventListener("change", update);
    };
  }, []);
  return dark;
}

/** 把当前主题写成 CSS 变量: styles/index.css 里那些 antd 管不到的地方读它。 */
function ThemeBridge({ dark }: { dark: boolean }) {
  const { token } = theme.useToken();
  useEffect(() => {
    const extra = dark ? EXTRA.dark : EXTRA.light;
    const root = document.documentElement;
    const vars: Record<string, string> = {
      "--forge-accent": token.colorPrimary,
      "--forge-border": token.colorBorder,
      "--forge-text": token.colorText,
      "--forge-text-soft": token.colorTextSecondary,
      "--forge-muted": token.colorTextTertiary,
      "--forge-danger": token.colorError,
      "--forge-warning": token.colorWarning,
      "--forge-raise": extra.raise,
      "--forge-code-bg": extra.codeBg,
      "--forge-code-inline": extra.codeInline,
      "--forge-shadow": extra.shadow,
    };
    for (const [name, value] of Object.entries(vars)) root.style.setProperty(name, value);
    // 页面加载与滚动回弹时露出来的就是 body, 让它和布局同色。
    document.body.style.background = token.colorBgLayout;
    // 滚动条与原生控件跟着一起换: 只改背景色的话, 深色底上会出现一条白色滚动条。
    root.style.colorScheme = dark ? "dark" : "light";
  }, [token, dark]);
  return null;
}

export function UiProvider({ children }: { children: ReactNode }) {
  const dark = useDarkTheme();
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
        token: { ...(dark ? DARK : LIGHT), borderRadius: 8, fontSize: 13 },
        components: {
          Menu: { itemMarginInline: 8, itemMarginBlock: 2, itemPaddingInline: 10, itemBorderRadius: 8 },
          Collapse: { contentPadding: 0 },
        },
      }}
    >
      <App component="div" style={{ height: "100%" }}>
        <ThemeBridge dark={dark} />
        {children}
      </App>
    </ConfigProvider>
  );
}
