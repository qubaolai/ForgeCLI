/** Utilities shared by the renderer and its document-local anchor plugin. */
import type { Element, Root, RootContent } from "hast";

export function nodeText(node: Root | RootContent): string {
  if (node.type === "text") return node.value;
  if (node.type === "element" && node.tagName === "img") return String(node.properties.alt ?? "");
  return "children" in node ? node.children.map(nodeText).join("") : "";
}

/** Keep heading links and footnote IDs inside their own conversation message. */
export function documentAnchors({ prefix }: { prefix: string }) {
  return (tree: Root) => {
    const elements: Element[] = [];
    function collect(node: Root | RootContent) {
      if (node.type === "element") elements.push(node);
      if ("children" in node) node.children.forEach(collect);
    }
    collect(tree);
    const targets = new Map<string, string>();
    const headings = new Map<string, string>();
    const used = new Set<string>();
    for (const node of elements) {
      const previous = node.properties.id;
      if (previous) {
        const original = String(previous);
        const id = original.startsWith(prefix) ? original : prefix + original;
        targets.set(original, id);
        node.properties.id = id;
        continue;
      }
      if (!/^h[1-6]$/.test(node.tagName)) continue;
      const slug =
        nodeText(node)
          .toLowerCase()
          .trim()
          .replace(/[^\p{L}\p{N}\p{M}_\s-]/gu, "")
          .replace(/\s/g, "-") || "section";
      let localId = slug;
      let duplicate = 0;
      while (used.has(localId)) localId = `${slug}-${++duplicate}`;
      used.add(localId);
      // A heading named "fn-a" must not take the ID of footnote a.
      const id = `${prefix}heading-${localId}`;
      headings.set(localId, id);
      node.properties.id = id;
    }
    for (const node of elements) {
      const href = node.properties.href;
      if (typeof href === "string" && href.startsWith("#")) {
        let fragment = href.slice(1);
        try {
          fragment = decodeURIComponent(fragment);
        } catch {
          /* Preserve malformed fragments. */
        }
        const footnote = node.properties.dataFootnoteRef || node.properties.dataFootnoteBackref;
        const id = footnote ? targets.get(fragment) : (headings.get(fragment) ?? targets.get(fragment));
        if (id) node.properties.href = `#${id}`;
      }
      const describedBy = node.properties.ariaDescribedBy;
      if (Array.isArray(describedBy)) {
        node.properties.ariaDescribedBy = describedBy.map((id) => targets.get(String(id)) ?? id);
      }
    }
  };
}
