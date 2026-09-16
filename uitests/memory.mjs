/**
 * The memory node, in a real browser.
 *
 * What the backend tests cannot see: that Memory is in the palette at all, that
 * it draws both ports so it can sit mid-line, that the canvas refuses a
 * mis-drawn belt *as it is drawn* rather than on the Run button, and — the one
 * that matters most — that what appears in the reading machine's thread is a
 * path and not the document.
 *
 * Run it:  docker compose --profile uitest run --rm uitest node tests/memory.mjs
 *
 * Same bargain as `loop.mjs`: its own throwaway project, deleted at the end,
 * and it asserts rather than prints.
 */

import { chromium } from "playwright";

const BASE = process.env.FACTOR_IA_URL ?? "http://127.0.0.1:8000";
const PROJECT = `uitest-memory-${Date.now().toString(36)}`;

let passed = 0;
function check(label, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `\n        expected ${JSON.stringify(expected)}\n        actual   ${JSON.stringify(actual)}`}`);
  if (!ok) process.exitCode = 1;
  else passed += 1;
}

const api = (path, init) =>
  fetch(`${BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", Origin: BASE, ...init?.headers },
  });

const codebases = await (await api("/api/codebases")).json();
if (codebases.length === 0) throw new Error("no codebase under /workspace to attach");
const codebase = codebases[0].id;

const created = await api("/api/projects", {
  method: "POST",
  body: JSON.stringify({ name: PROJECT, id: PROJECT, codebase_ids: [codebase] }),
});
if (!created.ok) throw new Error(`could not create the test project: ${await created.text()}`);

/**
 * The canonical shape, including the belt everybody forgets:
 *
 *   Request ──► Research ──► [Codebase index] ──► Planner ──► Commit
 *      └─────────────── the task ───────────────────┘
 *
 * The Planner reads a memory, so it also needs a belt saying what the job is —
 * a memory carries a reference, never a task. The reader runs the `echo`
 * script, which says its prompt back, so what it was *handed* is assertable on
 * screen rather than only in a run record.
 */
const graph = {
  nodes: [
    { id: "n-in", definition: { type: "input", label: "Request", position: { x: 0, y: 120 }, config: { kind: "request" } } },
    { id: "n-research", definition: { type: "agent", label: "Research", codebase_id: codebase, position: { x: 300, y: 0 }, config: { kind: "worker", adapter: "fake" } } },
    { id: "n-mem", definition: { type: "memory", label: "Codebase index", position: { x: 620, y: 0 }, config: {} } },
    { id: "n-plan", definition: { type: "agent", label: "Planner", codebase_id: codebase, position: { x: 940, y: 0 }, config: { kind: "worker", adapter: "fake", fake_script: "echo" } } },
    { id: "n-out", definition: { type: "output", label: "Commit", position: { x: 1260, y: 0 }, config: { kind: "commit" } } },
  ],
  edges: [
    { id: "e1", source: "n-in", target: "n-research" },
    { id: "e2", source: "n-research", target: "n-mem" },
    { id: "e3", source: "n-mem", target: "n-plan" },
    { id: "e4", source: "n-in", target: "n-plan" },
    { id: "e5", source: "n-plan", target: "n-out" },
  ],
};
const saved = await api(`/api/projects/${PROJECT}/factories/main`, {
  method: "PUT",
  body: JSON.stringify(graph),
});
if (!saved.ok) throw new Error(`could not save the graph: ${await saved.text()}`);

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 950 } });
  page.on("pageerror", (error) => {
    console.log(`FAIL  uncaught page error: ${error.message}`);
    process.exitCode = 1;
  });
  await page.goto(`${BASE}/#/p/${PROJECT}`, { waitUntil: "networkidle" });
  await page.waitForSelector(".node.type-memory");

  // ---- the machine --------------------------------------------------------
  check(
    "Memory is in the palette",
    await page.locator(".palette-item", { hasText: "Memory" }).count(),
    1,
  );
  check(
    "the node says what it is",
    (await page.locator(".node.type-memory .eyebrow").innerText()).toLowerCase(),
    "memory",
  );

  // Both ports, unnamed: a memory sits mid-line and has one way out, so a belt
  // out of it carries `source_handle: null` like every worker's.
  check(
    "a memory has one unnamed way out",
    await page
      .locator(".node.type-memory")
      .locator(".react-flow__handle-right")
      .evaluateAll((els) => els.map((el) => el.getAttribute("data-handleid"))),
    [null],
  );
  check(
    "…and something can feed it",
    await page.locator(".node.type-memory").locator(".react-flow__handle-left").count(),
    1,
  );

  // The name it is filed as, derived from the label rather than invented.
  check(
    "the name it will be filed as is shown, not left to be guessed",
    await page.locator(".node.type-memory .slot input").getAttribute("placeholder"),
    "codebase-index",
  );

  check("nothing blocks this factory", await page.locator(".problem.blocker").count(), 0);

  // ---- the rule everybody hits first --------------------------------------
  //
  // A machine fed only by memory has references and no task. The server refuses
  // it, but finding that out on the Run button is a worse way to learn a rule
  // than seeing it as the belt is drawn — so the canvas says it too.
  const withoutTask = { ...graph, edges: graph.edges.filter((edge) => edge.id !== "e4") };
  await api(`/api/projects/${PROJECT}/factories/main`, {
    method: "PUT",
    body: JSON.stringify(withoutTask),
  });
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".node.type-memory");
  await page.waitForTimeout(500);
  check(
    "a machine fed only by memory is refused before a run starts",
    await page.locator("text=/fed only by memory/").count() > 0,
    true,
  );

  await api(`/api/projects/${PROJECT}/factories/main`, {
    method: "PUT",
    body: JSON.stringify(graph),
  });
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".node.type-memory");
  await page.waitForTimeout(500);
  check("…and drawing the task belt settles it", await page.locator(".problem.blocker").count(), 0);

  // ---- the run ------------------------------------------------------------
  await page.locator(".runbox button.run").click();
  await page.waitForSelector(".request textarea");
  await page.locator(".request textarea").fill("Index this codebase.");
  await page.getByRole("button", { name: /Submit the request/ }).click();

  await page.waitForSelector('.runbox[data-run-status="succeeded"]', { timeout: 120_000 });
  check(
    "the memory ran",
    await page.locator(".node.type-memory").getAttribute("data-status"),
    "succeeded",
  );

  // **The claim the whole design rests on.** The reader echoes its prompt, so
  // what is on screen under the Planner is exactly what it was handed — and it
  // must be a path, not the document.
  const handed = await page.locator(".thread", { hasText: "Planner" }).innerText();
  check("the reader was told a memory exists", handed.includes("Memory available"), true);
  check("…and was given the file, not its contents", /_memory\/codebase-index\//.test(handed), true);
  check("…and was told it is reference, not instruction", handed.includes("reference"), true);

  // The run succeeded, so the document is gone: memory is RAM for a run.
  const runs = await (await api("/api/runs")).json();
  const record = runs.find((r) => r.project_id === PROJECT)?.memory?.[0];
  check("the run recorded its memory", record?.slug, "codebase-index");
  check("…and discarded it when it ended", record?.kept, false);
} finally {
  await browser.close();
  const removed = await api(`/api/projects/${PROJECT}`, { method: "DELETE" });
  check("the throwaway project deletes cleanly", removed.status, 204);
  console.log(`\n${passed} checks passed`);
}
