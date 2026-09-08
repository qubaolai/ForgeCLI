/** 渲染快照: 重构的安全网。
 *
 * 拆 JSX 最容易出的错 —— 少一个 className, 丢一个条件分支, 把两个兄弟节点写反 ——
 * 类型检查一个都拦不住, 肉眼也看不出来。这里把重构前的输出逐字节存下来, 之后每一步
 * 都要和它相等。
 *
 * 快照文件不存在时自动生成; 存在就只比对, 不覆盖。要重新生成就删掉 tests/snapshots/。
 */

import assert from "node:assert/strict";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { after, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

import * as fixture from "./fixtures.mjs";

// 组件位置只在这里写一次: 目录重排时只改这一段。
const MODULES = {
  runProcess: "/src/RunProcess.tsx",
  markdown: "/src/Markdown.tsx",
  runModel: "/src/runModel.ts",
  timeline: "/src/features/conversation/Timeline.tsx",
  promptCards: "/src/features/humanInteraction/PromptCards.tsx",
  settingsPanel: "/src/features/settings/SettingsPanel.tsx",
  modelSettings: "/src/features/settings/ModelSettings.tsx",
  projectPicker: "/src/features/chrome/ProjectPicker.tsx",
  confirmDialog: "/src/features/chrome/ConfirmDialog.tsx",
  modeMenu: "/src/features/chrome/ModeMenu.tsx",
  modelMenu: "/src/features/chrome/ModelMenu.tsx",
  planningPanel: "/src/features/planning/PlanningPanel.tsx",
};

const snapshotDir = join(dirname(fileURLToPath(import.meta.url)), "snapshots");
mkdirSync(snapshotDir, { recursive: true });

const server = await createServer({
  server: { middlewareMode: true, ws: false },
  optimizeDeps: { noDiscovery: true, include: [] },
  appType: "custom",
});
after(() => server.close());

const load = async (key) => server.ssrLoadModule(MODULES[key]);

const [
  runProcess,
  markdown,
  runModel,
  timeline,
  promptCards,
  settingsPanel,
  modelSettings,
  projectPicker,
  confirmDialog,
  modeMenu,
  modelMenu,
  planningPanel,
] = await Promise.all(
  [
    "runProcess",
    "markdown",
    "runModel",
    "timeline",
    "promptCards",
    "settingsPanel",
    "modelSettings",
    "projectPicker",
    "confirmDialog",
    "modeMenu",
    "modelMenu",
    "planningPanel",
  ].map(load),
);

// 时间冻住: 没有终态事件的那一轮用 now - startedAt 算耗时, 不冻住就每次跑都不一样。
Date.now = () => 1730000008400;

const noop = () => {};
const asyncNoop = async () => true;

/** 渲染一次, 与存档比对; 没有存档就先存一份。 */
function snapshot(name, element) {
  const html = renderToStaticMarkup(element);
  const file = join(snapshotDir, `${name}.html`);
  if (!existsSync(file)) {
    writeFileSync(file, html);
    return;
  }
  assert.equal(html, readFileSync(file, "utf8"), `${name} 的渲染结果与快照不一致`);
}

function settingsProps(extra) {
  const a = fixture.adminFixture;
  return {
    items: fixture.settings,
    roots: a.roots,
    rules: a.rules,
    checkpoints: a.checkpoints,
    providers: a.providers,
    providerSettings: a.providerSettings,
    providerFields: a.providerFields,
    modelFields: a.modelFields,
    knownProviders: a.knownProviders,
    providerProtocols: a.providerProtocols,
    llmRuntime: a.llmRuntime,
    catalog: a.catalog,
    actions: {
      onSetCurrentModel: asyncNoop,
      onSetOverride: noop,
      onClearOverride: noop,
      onPruneRules: noop,
      onUndo: noop,
      onPreviewCheckpoint: async () => "",
    },
    onClose: noop,
    onSave: noop,
    onReset: noop,
    onAddRoot: noop,
    onRemoveRoot: noop,
    onRevokeRule: noop,
    onRestore: noop,
    onAddModel: asyncNoop,
    onAddProvider: asyncNoop,
    onRemoveModel: noop,
    onSaveModel: noop,
    onSaveProvider: noop,
    onSaveLlmRuntime: noop,
    ...extra,
  };
}

const cases = () => [
  [
    "runProcess-completed",
    runProcess.RunProcess,
    { turn: fixture.completedTurn, directory: fixture.directory },
  ],
  ["runProcess-running", runProcess.RunProcess, { turn: fixture.runningTurn, directory: fixture.directory }],
  ["runProcess-failed", runProcess.RunProcess, { turn: fixture.failedTurn, directory: fixture.directory }],

  [
    "timeline-localTurn",
    timeline.LocalTurnView,
    { turn: fixture.completedTurn, directory: fixture.directory },
  ],
  [
    "timeline-restoredTurn",
    timeline.RestoredTurnView,
    {
      run: runModel.restoreTurn(
        { turn_id: "turn-1", events: fixture.completedEvents, outputs: fixture.completedOutputs },
        1730000000000,
      ),
      item: fixture.transcriptItem,
      directory: fixture.directory,
    },
  ],
  ["timeline-userMessage", timeline.Message, { item: fixture.userItem }],
  ["timeline-assistantMessage", timeline.Message, { item: fixture.transcriptItem }],

  ["approval-critical", promptCards.ApprovalCard, { approval: fixture.criticalApproval, onResolve: noop }],
  ["approval-calm", promptCards.ApprovalCard, { approval: fixture.calmApproval, onResolve: noop }],

  ["settings-general", settingsPanel.SettingsPanel, settingsProps({ initialTab: "general" })],
  ["settings-security", settingsPanel.SettingsPanel, settingsProps({ initialTab: "security" })],
  ["settings-recovery", settingsPanel.SettingsPanel, settingsProps({ initialTab: "recovery" })],
  ["settings-status", settingsPanel.SettingsPanel, settingsProps({ initialTab: "status" })],
  ["settings-models", settingsPanel.SettingsPanel, settingsProps({ initialTab: "models" })],

  ...["models", "providers", "gateway"].map((view) => [
    `modelSettings-${view}`,
    modelSettings.ModelSettings,
    {
      initialView: view,
      providers: fixture.adminFixture.providers,
      providerSettings: fixture.adminFixture.providerSettings,
      providerFields: fixture.adminFixture.providerFields,
      modelFields: fixture.adminFixture.modelFields,
      knownProviders: fixture.adminFixture.knownProviders,
      providerProtocols: fixture.adminFixture.providerProtocols,
      llmRuntime: fixture.adminFixture.llmRuntime,
      catalog: fixture.adminFixture.catalog,
      actions: {
        onSetCurrentModel: asyncNoop,
        onSetOverride: noop,
        onClearOverride: noop,
        onPruneRules: noop,
        onUndo: noop,
        onPreviewCheckpoint: async () => "",
      },
      onAddModel: asyncNoop,
      onAddProvider: asyncNoop,
      onRemoveModel: noop,
      onSaveModel: noop,
      onSaveProvider: noop,
      onSaveLlmRuntime: noop,
    },
  ]),

  [
    "projectPicker",
    projectPicker.ProjectPicker,
    {
      projects: fixture.projects,
      activeProjectId: "p-1",
      busy: false,
      trustPath: "/tmp/new",
      onTrustPath: noop,
      onTrust: noop,
      onActivate: noop,
      onCancel: noop,
    },
  ],
  [
    "confirmDialog",
    confirmDialog.ConfirmDialog,
    {
      title: "删除这个会话？",
      body: "不可撤销。",
      confirmLabel: "删除",
      busy: false,
      onConfirm: noop,
      onCancel: noop,
    },
  ],
  [
    "modeMenu",
    modeMenu.ModeMenu,
    {
      value: { sandbox: "workspace_write", approval: "always" },
      disabled: false,
      onChange: noop,
    },
  ],
  [
    "modelMenu",
    modelMenu.ModelMenu,
    {
      current: "deepseek:deepseek-chat",
      models: ["deepseek:deepseek-chat"],
      thinking: fixture.adminFixture.catalog.thinking,
      disabled: false,
      onChoose: noop,
      onThinking: noop,
    },
  ],
  [
    "planningPanel",
    planningPanel.PlanningPanel,
    {
      planning: fixture.planning,
      index: fixture.planIndex,
      onResolve: noop,
      onActivate: noop,
    },
  ],
  ["markdown", markdown.Markdown, { content: fixture.markdownSample }],
];

for (const [name, component, props] of cases()) {
  test(`renders ${name} exactly as before`, () => {
    snapshot(name, createElement(component, props));
  });
}
