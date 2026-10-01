/**
 * The factory library and its editor, in a real browser: made from the main
 * page, drawn on the restricted palette, simulated, and read back.
 *
 * What this checks is everything a factory is *before* a project uses it:
 *
 * - the Factories column sits after the projects, level with the skills and
 *   knowledge columns, each opened by the same add button;
 * - a new factory opens in its editor holding its six connectors, which the
 *   Delete key cannot remove;
 * - the palette offers agents and storage and nothing else;
 * - a belt drawn from the input entry lands on a machine's forward entry, and
 *   one from the loopback entry on its loop entry;
 * - Simulate asks for a codebase, takes its input in the chat, lights the
 *   connectors as the work passes through, and keeps nothing afterwards;
 * - a connector's log is what arrived there.
 *
 * `factory-node.mjs` is the companion: the same kind of factory, dropped onto a
 * project's line and run.
 *
 * Run it:  docker compose --profile uitest run --rm uitest node tests/factories.mjs
 *
 * Same bargain as the rest: its own throwaway factory, deleted at the end, and
 * it asserts rather than prints. Every agent is pinned to the fake adapter, so
 * nothing here spends a token.
 */

import { chromium } from "playwright";

const BASE = process.env.FACTOR_IA_URL ?? "http://127.0.0.1:8000";
const NAME = `uitest factory ${Date.now().toString(36)}`;

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
if (codebases.length === 0) throw new Error("no codebase under /workspace to simulate in");
const codebase = codebases[0].id;

let factoryId = null;
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 950 } });
  page.on("pageerror", (error) => {
    console.log(`FAIL  uncaught page error: ${error.message}`);
    process.exitCode = 1;
  });

  // ---- the main page ------------------------------------------------------
  await page.goto(`${BASE}/#/`, { waitUntil: "networkidle" });
  await page.waitForSelector("#factories-heading");
  const headings = await page.locator(".screen-inner h1, .screen-inner h2").allInnerTexts();
  const order = headings.map((text) => text.trim()).filter((text) => ["Projects", "Factories", "Skills"].some((word) => text.startsWith(word)));
  check("Factories come after Projects and before the libraries", order.slice(0, 2), ["Projects", "Factories"]);

  // The three libraries are columns at one level, each opened by the same
  // small add button — and the buttons stay level however long each lede runs.
  const columns = await page.evaluate(() =>
    [".factories-section", ".skills-section", ".knowledge-section"].map((selector) => {
      const column = document.querySelector(selector);
      const add = column?.querySelector(".library-body > .library-add");
      const box = (el) => el?.getBoundingClientRect();
      return {
        top: Math.round(box(column)?.top ?? -1),
        left: Math.round(box(column)?.left ?? -1),
        addTop: Math.round(box(add)?.top ?? -1),
        add: add?.textContent.replace("+", "").trim() ?? null,
      };
    }),
  );
  check("the three libraries share one row", new Set(columns.map((c) => c.top)).size, 1);
  check("left to right: factories, skills, knowledge",
    columns.every((c, i) => i === 0 || c.left > columns[i - 1].left), true);
  check("each opens with the same add button",
    columns.map((c) => c.add), ["New factory", "Import a skill", "New knowledge base"]);
  check("and the three add buttons are level", new Set(columns.map((c) => c.addTop)).size, 1);

  await page.locator(".factories-section .library-add").click();
  await page.locator('.factories-section input[aria-label="New factory name"]').fill(NAME);
  await page.locator(".factories-section button[type=submit]").click();
  await page.waitForFunction(() => window.location.hash.startsWith("#/f/"));
  factoryId = decodeURIComponent((await page.evaluate(() => window.location.hash)).slice(4));
  check("creating a factory opens its editor", factoryId.length > 0, true);

  // ---- the editor ---------------------------------------------------------
  await page.waitForSelector(".node.connector");
  check("a new factory holds its six connectors", await page.locator(".node.connector").count(), 6);
  const groups = await page
    .locator(".palette-group-trigger")
    .evaluateAll((els) => els.map((el) => el.getAttribute("data-group")));
  check("the palette offers agents and storage and nothing else", groups, ["agents", "storage"]);

  // A connector cannot be deleted: it is one of the factory's six ways in and out.
  await page.locator(".node.connector-entry.kind-forward").click();
  await page.keyboard.press("Backspace");
  await page.waitForTimeout(200);
  check("Delete does not remove a connector", await page.locator(".node.connector").count(), 6);

  // A worker, dropped in the middle of the sheet.
  await page.locator('.palette-group-trigger[data-group="agents"]').click();
  await page.locator(".palette-item.kind-worker").click();
  await page.keyboard.press("Escape");
  await page.waitForSelector(".node.type-agent");
  check("a worker in a factory draws no codebase plate", await page.locator('.node.type-agent select[aria-label="attached codebase"]').count(), 0);

  const centre = async (selector) => {
    const box = await page.locator(selector).first().boundingBox();
    if (box === null) throw new Error(`nothing at ${selector}`);
    return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  };
  const drag = async (from, to) => {
    const a = await centre(from);
    const b = await centre(to);
    await page.mouse.move(a.x, a.y);
    await page.mouse.down();
    await page.mouse.move(b.x, b.y, { steps: 14 });
    await page.mouse.up();
    await page.waitForTimeout(150);
  };
  await drag(
    ".node.connector-entry.kind-forward .react-flow__handle-right",
    '.node.type-agent .react-flow__handle[data-handleid="forward"]',
  );
  await drag(
    ".node.connector-entry.kind-loop .react-flow__handle-right",
    '.node.type-agent .react-flow__handle[data-handleid="loop"]',
  );
  await drag(
    ".node.type-agent .react-flow__handle-right",
    ".node.connector-exit.kind-forward .react-flow__handle-left",
  );
  check("three belts are drawn", await page.locator(".react-flow__edge").count(), 3);

  await page.locator(".savebar button.primary").click();
  await page.waitForSelector(".pill.saved");
  const stored = await (await api(`/api/factories/${factoryId}`)).json();
  const worker = stored.nodes.find((node) => node.definition.type === "agent");
  const into = (source) => stored.edges.find((edge) => edge.source === source && edge.target === worker.id);
  check("the file is a library factory", stored.project_id, null);
  check("the worker carries no codebase", worker.definition.codebase_id, null);
  check("the input entry's belt lands on the forward entry", into("entry-forward")?.target_handle, "forward");
  check("the loopback entry's belt lands on the loop entry", into("entry-loop")?.target_handle, "loop");

  // Pinned to the fake adapter before anything runs — nothing here spends a token.
  worker.definition.config = { ...worker.definition.config, adapter: "fake" };
  const pinned = await api(`/api/factories/${factoryId}`, {
    method: "PUT",
    body: JSON.stringify({ nodes: stored.nodes, edges: stored.edges }),
  });
  if (!pinned.ok) throw new Error(`could not pin the fake adapter: ${await pinned.text()}`);
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".simulate-box");
  check("every edge is still drawn after a reload", await page.locator(".react-flow__edge").count(), 3);

  // ---- Simulate -------------------------------------------------------------
  const simulate = page.locator(".simulate-box .runbox-head button.simulate");
  await page.locator('.simulate-box select[aria-label="codebase to simulate in"]').selectOption(codebase);
  check("Simulate is offered once a codebase is chosen", await simulate.isEnabled(), true);
  const overflow = await page
    .locator(".simulate-box")
    .evaluate((box) => box.scrollWidth - box.clientWidth);
  check("the run panel does not scroll sideways", overflow <= 0, true);
  await simulate.click();
  await page.waitForSelector(".request textarea", { timeout: 10000 });
  await page.locator(".request textarea").fill("tidy the readme");
  await page.locator(".request button[type=submit]").click();

  const status = () => page.locator(".simulate-box").getAttribute("data-run-status");
  const deadline = Date.now() + 30000;
  let reached = await status();
  while (!["succeeded", "failed", "cancelled"].includes(reached ?? "")) {
    if (Date.now() > deadline) throw new Error(`the simulation never finished — it is ${reached}`);
    await page.waitForTimeout(250);
    reached = await status();
  }
  check("the simulation succeeds", reached, "succeeded");
  check(
    "the input entry passed the work in",
    await page.locator(".node.connector-entry.kind-forward").getAttribute("data-status"),
    "succeeded",
  );
  check(
    "the input exit passed it out",
    await page.locator(".node.connector-exit.kind-forward").getAttribute("data-status"),
    "succeeded",
  );

  // What arrived at the input exit is its log.
  await page.locator(".node.connector-exit.kind-forward .node-log-open").click();
  await page.waitForSelector(".node-log");
  check("a connector's log opens", await page.locator(".node-log").count(), 1);
  await page.locator(".node-log-close").click();

  const runs = await (await api("/api/runs?limit=20")).json();
  const run = runs.find((one) => one.kind === "simulation" && one.factory_id === factoryId);
  check("the run is recorded as a simulation", run?.kind, "simulation");
  check("…with no project", run?.project_id, null);
  check("…and no branch", run?.branch, null);
  check("…and nothing kept", (run?.worktrees ?? []).map((made) => made.kept), [false]);
} finally {
  await browser.close();
  if (factoryId) {
    const removed = await api(`/api/factories/${factoryId}`, { method: "DELETE" });
    check("the throwaway factory deletes cleanly", removed.status, 204);
  }
  console.log(`\n${passed} checks passed`);
}
