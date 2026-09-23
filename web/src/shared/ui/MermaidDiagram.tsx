import { useEffect, useMemo, useState } from "react";
import { theme } from "antd";
import { renderMermaid } from "@/shared/lib/mermaid";
import { CopyButton } from "@/shared/ui/CopyButton";

type RenderResult = { key: string; url?: string; error?: string };

export function MermaidDiagram({ source }: { source: string }) {
  const { token } = theme.useToken();
  const palette = useMemo(
    () => ({
      background: token.colorBgContainer,
      text: token.colorText,
      border: token.colorBorder,
      accent: token.colorPrimary,
    }),
    [token.colorBgContainer, token.colorText, token.colorBorder, token.colorPrimary],
  );
  const key = JSON.stringify([source, palette]);
  const [result, setResult] = useState<RenderResult>();
  const [showSource, setShowSource] = useState(false);
  const current = result?.key === key ? result : undefined;

  useEffect(() => {
    const controller = new AbortController();
    // Coalesce token deltas; ignore work superseded by a newer source or theme.
    const timer = window.setTimeout(() => {
      void renderMermaid(source, palette, controller.signal).then(
        (url) => {
          if (url && !controller.signal.aborted) setResult({ key, url });
        },
        () => {
          if (!controller.signal.aborted)
            setResult({ key, error: "暂时无法渲染图表，内容可能尚未完整或存在语法错误。" });
        },
      );
    }, 250);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [source, palette, key]);

  return (
    <figure className="forge-mermaid">
      <figcaption>
        <span>Mermaid</span>
        <div className="forge-mermaid-actions">
          <button type="button" onClick={() => setShowSource(!showSource)} aria-pressed={showSource}>
            {showSource ? "查看图表" : "查看源码"}
          </button>
          <CopyButton content={source} compact label="复制图表源码" />
        </div>
      </figcaption>
      {!showSource && current?.url && (
        <div className="forge-mermaid-preview">
          <img src={current.url} alt="Mermaid 图表" />
        </div>
      )}
      {!showSource && !current?.url && (
        <p className="forge-mermaid-status" role="status">
          {current?.error ?? "正在生成图表…"}
        </p>
      )}
      {(showSource || current?.error) && (
        <pre>
          <code>{source}</code>
        </pre>
      )}
    </figure>
  );
}
