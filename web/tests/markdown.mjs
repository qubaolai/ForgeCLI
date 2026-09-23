import assert from "node:assert/strict";
import { after, test } from "node:test";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createElement, Fragment } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

// Compile the production renderer; keep bare imports resolvable without a second bundler.
const cache = fileURLToPath(new URL("../node_modules/.cache/", import.meta.url));
await mkdir(cache, { recursive: true });
const temp = await mkdtemp(join(cache, "forge-markdown-tests-"));
after(() => rm(temp, { recursive: true, force: true }));
await mkdir(join(temp, "lib"));
await mkdir(join(temp, "ui"));
for (const file of [
  "lib/markdown.ts",
  "lib/mermaid.ts",
  "ui/Markdown.tsx",
  "ui/MermaidDiagram.tsx",
  "ui/CopyButton.tsx",
]) {
  const source = await readFile(new URL(`../src/shared/${file}`, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  });
  await writeFile(
    join(temp, file.replace(/\.tsx?$/, ".mjs")),
    outputText.replaceAll(/@\/shared\/(lib|ui)\/([\w-]+)/g, "../$1/$2.mjs"),
  );
}
const { Markdown } = await import(pathToFileURL(join(temp, "ui/Markdown.mjs")));
const { nodeText } = await import(pathToFileURL(join(temp, "lib/markdown.mjs")));
const { renderMermaid } = await import(pathToFileURL(join(temp, "lib/mermaid.mjs")));
const render = (content) => renderToStaticMarkup(createElement(Markdown, { content }));

test("Mermaid skips cancelled work and a rejected diagram does not block later requests", async () => {
  const palette = { background: "#fff", text: "#222", border: "#ddd", accent: "#168665" };
  const cancelled = new AbortController();
  cancelled.abort();
  assert.equal(await renderMermaid("flowchart LR\n A --> B", palette, cancelled.signal), null);
  await assert.rejects(renderMermaid("x".repeat(50_001), palette, new AbortController().signal), /源码过长/);
  assert.equal(await renderMermaid("flowchart LR\n B --> C", palette, cancelled.signal), null);
});

test("Mermaid fences use the diagram component while other code remains ordinary code", () => {
  for (const language of ["mermaid", "Mermaid"]) {
    const html = render("```" + language + "\nflowchart LR\n A --> B\n```");
    assert.match(html, /class="forge-mermaid"/);
    assert.match(html, /查看源码/);
    assert.match(html, /复制图表源码/);
    assert.match(html, /role="status">正在生成图表/);
  }
  assert.doesNotMatch(render("```text\nflowchart LR\n A --> B\n```"), /class="forge-mermaid"/);
  assert.doesNotMatch(render("`mermaid`"), /class="forge-mermaid"/);
});

test("CommonMark preserves nested blocks, list starts, emphasis and all heading levels", () => {
  const html = render(
    [
      "Title\n=====",
      "## 二级",
      "### 三",
      "#### 四",
      "##### 五",
      "###### 六",
      "3. parent\n   - child **bold _italic_**\n\n     second paragraph\n4. next",
      "> outer\n>\n> > inner",
      "~~removed~~ and \\*literal\\*",
      "line  \nbreak",
      "---",
    ].join("\n\n"),
  );
  for (let level = 1; level <= 6; level++) assert.match(html, new RegExp(`<h${level} id=`));
  assert.match(html, /<ol start="3">/);
  assert.match(
    html,
    /parent\s*<ul>\s*<li>\s*<p>child <strong>bold <em>italic<\/em><\/strong><\/p>\s*<p>second paragraph<\/p>/,
  );
  assert.match(html, /<blockquote>\s*<p>outer<\/p>\s*<blockquote>/);
  assert.match(html, /<del>removed<\/del> and \*literal\*/);
  assert.match(html, /line<br\/>\nbreak/);
  assert.match(html, /<hr\/>/);
});

test("GFM tables retain alignment, escaped pipes and task list state", () => {
  const html = render(
    "| Left | Right | Center |\n| :--- | ---: | :---: |\n| **yes** | `a\\|b` | z |\n\n- [x] done\n- [ ] pending",
  );
  assert.match(html, /class="forge-md-table" role="region"[^>]*tabindex="0"><table>/);
  assert.match(html, /<th style="text-align:right">Right<\/th>/);
  assert.match(html, /<td style="text-align:center">z<\/td>/);
  assert.match(html, /<code>a\|b<\/code>/);
  assert.match(html, /<input type="checkbox" disabled="" checked=""\/> done/);
  assert.match(html, /<input type="checkbox" disabled=""\/> pending/);
});

test("fenced and indented code preserve contents with highlighting and unknown-language fallback", () => {
  const html = render(
    '~~~js\nconst x = "<tag>";\n~~~\n\n````unknownlang\n```nested```\n````\n\n    a < b\n\n`**literal**`',
  );
  assert.match(html, /<figure class="forge-code-block">/);
  assert.match(html, /class="hljs-keyword">const<\/span>/);
  assert.match(html, /&quot;&lt;tag&gt;&quot;/);
  assert.match(html, /class="[^"]*language-unknownlang">```nested```\n<\/code>/);
  assert.match(html, /<pre><code>a &lt; b\n<\/code><\/pre>/);
  assert.match(html, /<code>\*\*literal\*\*<\/code>/);
  // This is the extraction used for copying highlighted code, including whitespace.
  assert.equal(
    nodeText({
      type: "element",
      tagName: "code",
      properties: {},
      children: [
        { type: "element", tagName: "span", properties: {}, children: [{ type: "text", value: "const" }] },
        { type: "text", value: ' x = "<tag>";\n  next\n' },
      ],
    }),
    'const x = "<tag>";\n  next\n',
  );
});

test("links, autolinks, reference links and images render without executable HTML or URLs", () => {
  const html = render(
    '[ref][doc] https://example.org\n\n[doc]: https://example.com "Docs"\n\n![diagram](/diagram.png "Diagram")\n\n[mail](mailto:a@example.com) [relative](./file.md)\n\n[bad](javascript:alert%281%29) [encoded](java&#x73;cript:alert%281%29) ![bad image](data:image/svg+xml,bad)\n\n<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>',
  );
  assert.match(html, /href="https:\/\/example.com" title="Docs" target="_blank" rel="noopener noreferrer"/);
  assert.match(html, /href="https:\/\/example.org"/);
  assert.match(
    html,
    /<img src="\/diagram.png" alt="diagram" title="Diagram" loading="lazy" decoding="async"\/>/,
  );
  assert.match(html, /<a href="mailto:a@example.com">mail<\/a>/);
  assert.match(html, /<a href="\.\/file.md">relative<\/a>/);
  assert.doesNotMatch(html, /(?:href|src)="(?:javascript|data):/i);
  assert.doesNotMatch(html, /<script|<img[^>]*onerror/i);
  assert.match(html, /&lt;script&gt;/);
});

test("heading and footnote anchors stay within their message, including duplicate headings", () => {
  const content =
    "# 标题\n\n# 标题\n\n[one](#%E6%A0%87%E9%A2%98) [two](#标题-1) note[^a] again[^a]\n\n[^a]: footnote";
  const html = renderToStaticMarkup(
    createElement(Fragment, null, createElement(Markdown, { content }), createElement(Markdown, { content })),
  );
  const messages = html.split('<div class="forge-md">').slice(1);
  const allIds = [...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(new Set(allIds).size, allIds.length);
  for (const message of messages) {
    const ids = [...message.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
    const anchors = [...message.matchAll(/href="#([^"]+)"/g)].map((match) => match[1]);
    assert.ok(anchors.length >= 6);
    for (const anchor of anchors) assert.ok(ids.includes(anchor), `missing local target ${anchor}`);
    assert.match(message, /href="#[^"]*标题-1">two<\/a>/);
    for (const [, label] of message.matchAll(/aria-describedby="([^"]+)"/g)) assert.ok(ids.includes(label));
  }
});

test("headings cannot steal footnote IDs or accessibility labels", () => {
  const html = render(
    "# fn-a\n\n# footnote-label\n\n[heading](#fn-a) [label heading](#footnote-label) note[^a]\n\n[^a]: definition",
  );
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(new Set(ids).size, ids.length);
  assert.match(html, /href="#[^"]*heading-fn-a">heading<\/a>/);
  assert.match(html, /href="#[^"]*heading-footnote-label">label heading<\/a>/);
  assert.match(html, /href="#[^"]*-fn-a" id="[^"]*-fnref-a"/);
  assert.match(html, /aria-describedby="forge-md-[^"]*-footnote-label"/);
});

test("partial streamed Markdown renders throughout incomplete fences, links, tables and lists", () => {
  const content =
    '# 流式\n\n**bold _nested_** [link](https://example.com)\n\n| a | b |\n| --- | --- |\n| x | y |\n\n- [x] done\n  - nested\n\n```js\nconst x = "hello";\n```';
  for (let end = 0; end <= content.length; end++) assert.doesNotThrow(() => render(content.slice(0, end)));
});
