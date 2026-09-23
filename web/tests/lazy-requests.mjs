import assert from "node:assert/strict";
import { after, test } from "node:test";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const cache = fileURLToPath(new URL("../node_modules/.cache/", import.meta.url));
await mkdir(cache, { recursive: true });
const temp = await mkdtemp(join(cache, "forge-request-tests-"));
after(() => rm(temp, { recursive: true, force: true }));
await writeFile(
  join(temp, "api.mjs"),
  `
export const calls = [];
export async function api(path, options) {
  calls.push([path, options?.method ?? "GET"]);
  return { items: [], all: [], thinking: {}, provider_settings: [], plan: {} };
}
export function indexTools() { return {}; }
`,
);
for (const [name, source] of [
  ["admin", "features/administration/useAdministration.ts"],
  ["planning", "features/planning/usePlanning.ts"],
]) {
  const text = await readFile(new URL(`../src/${source}`, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(text, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
  });
  await writeFile(
    join(temp, `${name}.mjs`),
    outputText
      .replaceAll("@/shared/api/client", "./api.mjs")
      .replaceAll("@/shared/lib/run/tools", "./api.mjs"),
  );
}
const { calls } = await import(pathToFileURL(join(temp, "api.mjs")));
const { useAdministration } = await import(pathToFileURL(join(temp, "admin.mjs")));
const { usePlanning } = await import(pathToFileURL(join(temp, "planning.mjs")));
let admin, planning;
function Harness() {
  const fail = (message) => assert.fail(message);
  admin = useAdministration(fail, async () => {});
  planning = usePlanning(fail);
  return null;
}
renderToStaticMarkup(createElement(Harness));
assert.deepEqual(calls, []);

async function expectCalls(action, expected) {
  calls.length = 0;
  await action();
  assert.deepEqual(calls, expected);
}

test("settings loaders request only the selected section", async () => {
  await expectCalls(admin.loadRecovery, [["/rewind", "GET"]]);
  await expectCalls(admin.loadStatus, [["/status", "GET"]]);
  await expectCalls(admin.loadModels, [["/models", "GET"]]);
  await expectCalls(admin.loadSecurity, [
    ["/workspace-roots", "GET"],
    ["/permissions", "GET"],
    ["/tools", "GET"],
  ]);
});

test("writes refresh only data belonging to the operation", async () => {
  await expectCalls(
    () => admin.addProvider({}),
    [
      ["/providers", "POST"],
      ["/models", "GET"],
    ],
  );
  await expectCalls(
    () => admin.rewindTo("message"),
    [
      ["/rewind/message", "POST"],
      ["/rewind", "GET"],
    ],
  );
  await expectCalls(
    () => admin.removeRule("deny", "session", "Read"),
    [
      ["/permissions/remove", "POST"],
      ["/workspace-roots", "GET"],
      ["/permissions", "GET"],
      ["/tools", "GET"],
    ],
  );
});

test("background planning sync does not fetch the plan directory", async () => {
  await expectCalls(planning.load, [["/planning", "GET"]]);
  await expectCalls(planning.loadIndex, [["/plans", "GET"]]);
  await expectCalls(
    () => planning.activatePlan("plan"),
    [
      ["/plans/plan/activate", "POST"],
      ["/planning", "GET"],
    ],
  );
});
