import { Fragment, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { MarkdownBlock as Block, parseMarkdown } from "./markdownModel";

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

export function CopyButton({
  content,
  className = "",
  compact = false,
  label = "复制",
}: {
  content: string;
  className?: string;
  compact?: boolean;
  label?: string;
}) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const resetTimer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (resetTimer.current !== null) window.clearTimeout(resetTimer.current);
    },
    [],
  );

  async function copy() {
    try {
      await navigator.clipboard.writeText(content);
      setState("copied");
    } catch {
      setState("failed");
    }
    if (resetTimer.current !== null) window.clearTimeout(resetTimer.current);
    resetTimer.current = window.setTimeout(() => setState("idle"), 1600);
  }

  const text = state === "copied" ? "已复制" : state === "failed" ? "复制失败" : label;
  return (
    <button
      type="button"
      className={`copy-button ${state} ${className}`}
      onClick={copy}
      aria-label={text}
      title={text}
    >
      <CopyIcon checked={state === "copied"} />
      {!compact && <span>{text}</span>}
    </button>
  );
}

function CopyIcon({ checked }: { checked: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      {checked ? (
        <path d="m5 12 4 4L19 6" />
      ) : (
        <>
          <rect x="8" y="8" width="11" height="11" rx="2" />
          <path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" />
        </>
      )}
    </svg>
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
