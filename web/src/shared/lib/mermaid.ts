/** Mermaid has global configuration: serialize initialization and rendering across messages. */
export type DiagramPalette = { background: string; text: string; border: string; accent: string };

let queue: Promise<unknown> = Promise.resolve();
let sequence = 0;

export function renderMermaid(source: string, palette: DiagramPalette, signal: AbortSignal) {
  const task = queue.then(async () => {
    if (signal.aborted) return null;
    if (source.length > 50_000) throw new Error("图表源码过长，请拆分为多个图表。");
    const [{ default: mermaid }, { default: DOMPurify }] = await Promise.all([
      import("mermaid"),
      import("dompurify"),
    ]);
    if (signal.aborted) return null;
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      suppressErrorRendering: true,
      maxTextSize: 50_000,
      maxEdges: 500,
      theme: "base",
      htmlLabels: false,
      flowchart: { htmlLabels: false },
      fontFamily: "Arial, sans-serif",
      // Diagram directives must not relax security or enable HTML labels.
      secure: [
        "secure",
        "securityLevel",
        "startOnLoad",
        "maxTextSize",
        "maxEdges",
        "suppressErrorRendering",
        "htmlLabels",
        "flowchart",
        "dompurifyConfig",
        "themeCSS",
        "themeVariables",
      ],
      themeVariables: {
        background: palette.background,
        primaryColor: palette.background,
        secondaryColor: palette.background,
        tertiaryColor: palette.background,
        primaryTextColor: palette.text,
        secondaryTextColor: palette.text,
        tertiaryTextColor: palette.text,
        primaryBorderColor: palette.accent,
        lineColor: palette.text,
        textColor: palette.text,
        clusterBkg: palette.background,
        clusterBorder: palette.border,
        edgeLabelBackground: palette.background,
        actorBkg: palette.background,
        actorTextColor: palette.text,
        actorBorder: palette.accent,
        actorLineColor: palette.text,
        signalColor: palette.text,
        signalTextColor: palette.text,
        labelBoxBkgColor: palette.background,
        labelBoxBorderColor: palette.border,
        labelTextColor: palette.text,
        loopTextColor: palette.text,
        noteBkgColor: palette.background,
        noteBorderColor: palette.border,
        noteTextColor: palette.text,
      },
    });
    const host = document.createElement("div");
    host.className = "forge-mermaid-measure";
    host.setAttribute("aria-hidden", "true");
    document.body.append(host);
    try {
      const { svg } = await mermaid.render(`forge-diagram-${++sequence}`, source, host);
      if (signal.aborted) return null;
      const clean = DOMPurify.sanitize(svg, {
        USE_PROFILES: { svg: true, svgFilters: true },
        FORBID_TAGS: ["foreignObject", "script", "image"],
      });
      const diagram = new DOMParser().parseFromString(clean, "image/svg+xml").documentElement;
      if (diagram.tagName !== "svg") throw new Error("无效的图表 SVG。");
      const bounds = diagram
        .getAttribute("viewBox")
        ?.trim()
        .split(/[\s,]+/)
        .map(Number);
      if (bounds?.length === 4 && bounds.slice(2).every((size) => Number.isFinite(size) && size > 0)) {
        // Mermaid's percentage width has no intrinsic size when used as an image.
        diagram.setAttribute("width", String(bounds[2]));
        diagram.setAttribute("height", String(bounds[3]));
      }
      // An SVG image isolates diagram CSS and disables scripts and clickable nodes.
      return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(new XMLSerializer().serializeToString(diagram))}`;
    } finally {
      host.remove();
    }
  });
  queue = task.catch(() => undefined);
  return task;
}
