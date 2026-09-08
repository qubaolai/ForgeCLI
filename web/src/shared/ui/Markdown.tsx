import { Fragment, ReactNode, useMemo } from "react";
import { MarkdownBlock as Block, parseMarkdown } from "@/shared/lib/markdown";
import { CopyButton } from "@/shared/ui/CopyButton";

type MarkdownProps = {
  content: string;
  compact?: boolean;
};

export function Markdown({ content, compact = false }: MarkdownProps) {
  const blocks = useMemo(() => parseMarkdown(content), [content]);
  return (
    <div className={`markdown-body ${compact ? "compact" : ""}`}>
      {blocks.map((block, index) => (
        <MarkdownBlock block={block} key={`${block.kind}-${index}`} />
      ))}
    </div>
  );
}

function MarkdownBlock({ block }: { block: Block }) {
  if (block.kind === "code") {
    return (
      <figure className="markdown-code">
        <figcaption>
          <span>{block.language || "code"}</span>
          <CopyButton content={block.text} className="code-copy" compact label="复制代码" />
        </figcaption>
        <pre>
          <code>{block.text}</code>
        </pre>
      </figure>
    );
  }
  if (block.kind === "heading") {
    if (block.level === 1) return <h1>{renderInline(block.text)}</h1>;
    if (block.level === 2) return <h2>{renderInline(block.text)}</h2>;
    if (block.level === 3) return <h3>{renderInline(block.text)}</h3>;
    return <h4>{renderInline(block.text)}</h4>;
  }
  if (block.kind === "list") {
    const items = block.items.map((item, index) => <li key={`${index}-${item}`}>{renderInline(item)}</li>);
    return block.ordered ? <ol>{items}</ol> : <ul>{items}</ul>;
  }
  if (block.kind === "quote") return <blockquote>{renderLines(block.text)}</blockquote>;
  if (block.kind === "rule") return <hr />;
  return <p>{renderLines(block.text)}</p>;
}

function renderLines(text: string) {
  return text.split("\n").map((line, index) => (
    <Fragment key={`${index}-${line}`}>
      {index > 0 && <br />}
      {renderInline(line)}
    </Fragment>
  ));
}

function renderInline(text: string): ReactNode[] {
  const tokens = text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g).filter(Boolean);
  return tokens.map((token, index) => {
    if (token.startsWith("**") && token.endsWith("**"))
      return <strong key={index}>{token.slice(2, -2)}</strong>;
    if (token.startsWith("`") && token.endsWith("`")) return <code key={index}>{token.slice(1, -1)}</code>;
    const link = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (link && safeLink(link[2]))
      return (
        <a href={link[2]} target="_blank" rel="noreferrer" key={index}>
          {link[1]}
        </a>
      );
    return <Fragment key={index}>{token}</Fragment>;
  });
}

function safeLink(href: string) {
  return /^(https?:\/\/|\/)/.test(href);
}
