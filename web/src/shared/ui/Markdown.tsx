/** CommonMark + GFM, rendered as React elements rather than injected HTML. */
import { memo, useId, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import type { Components, Options } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { documentAnchors, nodeText } from "@/shared/lib/markdown";
import { CopyButton } from "@/shared/ui/CopyButton";
import { MermaidDiagram } from "@/shared/ui/MermaidDiagram";

const components: Components = {
  pre({ node, children, ...props }) {
    const code = node?.children.find((child) => child.type === "element" && child.tagName === "code");
    const classes = code?.type === "element" ? code.properties.className : [];
    const language = Array.isArray(classes)
      ? String(classes.find((name) => String(name).startsWith("language-")) ?? "").slice(9)
      : "";
    // Text is read from the syntax tree so highlighting never changes copied code.
    const text = code ? nodeText(code).replace(/\n$/, "") : "";
    if (language.toLowerCase() === "mermaid") return <MermaidDiagram source={text} />;
    return (
      <figure className="forge-code-block">
        <figcaption>
          <span>{language || "code"}</span>
          <CopyButton content={text} compact label="复制代码" />
        </figcaption>
        <pre {...props}>{children}</pre>
      </figure>
    );
  },
  table({ children }) {
    return (
      <div className="forge-md-table" role="region" aria-label="表格，可横向滚动" tabIndex={0}>
        <table>{children}</table>
      </div>
    );
  },
  a({ href, children, title, ...props }) {
    if (!href) return <span>{children}</span>;
    const external = /^(?:https?:)?\/\//i.test(href);
    return (
      <a
        href={href}
        title={title}
        id={props.id}
        aria-describedby={props["aria-describedby"]}
        aria-label={props["aria-label"]}
        className={props.className}
        target={external ? "_blank" : undefined}
        rel={external ? "noopener noreferrer" : undefined}
      >
        {children}
      </a>
    );
  },
  img({ src, alt, title }) {
    if (!src) return <span>{alt}</span>;
    return <img src={src} alt={alt ?? ""} title={title} loading="lazy" decoding="async" />;
  },
};

export const Markdown = memo(function Markdown({ content }: { content: string }) {
  const id = useId();
  const prefix = `forge-md-${id.replace(/[^a-zA-Z0-9_-]/g, "")}-`;
  const plugins = useMemo<Options["rehypePlugins"]>(
    () => [
      [documentAnchors, { prefix }],
      [rehypeHighlight, { detect: false }],
    ],
    [prefix],
  );
  return (
    <div className="forge-md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={plugins}
        remarkRehypeOptions={{ clobberPrefix: prefix, footnoteLabel: "注释", footnoteBackLabel: "返回正文" }}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});
