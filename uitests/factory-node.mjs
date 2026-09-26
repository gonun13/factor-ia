/**
 * A factory node on a project's line, in a real browser: drawn, run, and opened.
 *
 * - The factory node draws a machine's three entries and three exits of its
 *   own — memory, input and loopback on both sides.
 * - The palette's Factories folder offers the library.
 * - A file whose loop belt into the factory node names no entry (every file
 *   written before entries existed) still draws every edge, and heals onto the
 *   loop entry on the next save — `decisions/0012`'s trap, on the new node type.
 * - A run goes into the factory and comes out: the factory node lights up as
 *   the sum of the machines inside it, which the project canvas never draws.
 * - Opening the node during the run shows those machines, lit by the same run,
 *   and closing it returns to the project canvas.
 *
 * Run it:  docker compose --profile uitest run --rm uitest node tests/factory-node.mjs
 *
 * Its own throwaway project and factory, deleted at the end. Every agent is
 * pinned to the fake adapter.
 */

import { chromium } from "playwright";

const BASE = process.env.FACTOR_IA_URL ?? "http://127.0.0.1:8000";
const STAMP = Date.now().toString(36);
const PROJECT = `uitest-factory-node-${STAMP}`;
const FACTORY = `uitest-block-${STAMP}`;

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

// ---- the factory: work, which a loop may send back ------------------------
const made = await api("/api/factories", {
  method: "POST",
  body: JSON.stringify({ name: `Block ${STAMP}`, id: FACTORY }),
});
if (!made.ok) throw new Error(`could not create the test factory: ${await made.text()}`);
const body = await made.json();
body.nodes.push({
  id: "work",
  definition: { type: "agent", label: "Work", position: { x: 450, y: 240 }, config: { kind: "worker", adapter: "fake" } },
});
body.edges.push(
  { id: "b1", source: "entry-forward", target: "work", target_handle: "forward" },
  { id: "b2", source: "entry-loop", target: "work", target_handle: "loop" },
  { id: "b3", source: "work", target: "exit-forward" },
);
const filled = await api(`/api/factories/${FACTORY}`, {
  method: "PUT",
  body: JSON.stringify({ nodes: body.nodes, edges: body.edges }),
});
if (!filled.ok) throw new Error(`could not fill the test factory: ${await filled.text()}`);

// ---- the project: request → factory → review → discard, and a loop back ----
const created = await api("/api/projects", {
  method: "POST",
  body: JSON.stringify({ name: PROJECT, id: PROJECT, codebase_ids: [codebase] }),
});
if (!created.ok) throw new Error(`could not create the test project: ${await created.text()}`);
const graph = {
  nodes: [
    { id: "n-in", definition: { type: "input", label: "Request", position: { x: 0, y: 200 }, config: { kind: "request" } } },
    { id: "n-block", definition: { type: "factory-ref", label: "Block", codebase_id: codebase, factory_ref: FACTORY, position: { x: 340, y: 160 }, config: {} } },
    { id: "n-review", definition: { type: "agent", label: "Review", codebase_id: codebase, position: { x: 720, y: 200 }, config: { kind: "reviewer", adapter: "fake", fake_scripts: ["verdict-pass"] } } },
    { id: "n-out", definition: { type: "output", label: "Discard", position: { x: 1080, y: 200 }, config: { kind: "discard" } } },
  ],
  edges: [
    { id: "e1", source: "n-in", target: "n-block" },
    { id: "e2", source: "n-block", target: "n-review", source_handle: "success" },
    { id: "e3", source: "n-review", target: "n-out", source_handle: "success" },
    // Names no entry, as every file written before entries did.
    { id: "e4", source: "n-review", target: "n-block", source_handle: "fail" },
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
  await page.waitForSelector(".node.type-factory");

  // ---- what is drawn --------------------------------------------------------
  const handles = (side) =>
    page
      .locator(".node.type-factory")
      .locator(`.react-flow__handle-${side}`)
      .evaluateAll((els) => els.map((el) => el.getAttribute("data-handleid")).sort());
  check("a factory node draws three entries", await handles("left"), ["forward", "loop", "store"]);
  check("…and three exits", await handles("right"), ["fail", "store", "success"]);
  check("every edge in the file is drawn", await page.locator(".react-flow__edge").count(), graph.edges.length);

  await page.locator('.palette-group-trigger[data-group="factories"]').click();
  check(
    "the Factories folder offers the library",
    await page.locator(`.palette-item[data-kind="${FACTORY}"]`).count(),
    1,
  );
  await page.keyboard.press("Escape");

  // Nudge a node so the healed entries are saved.
  const review = await page.locator(".node.type-agent").boundingBox();
  await page.mouse.move(review.x + 40, review.y + 12);
  await page.mouse.down();
  await page.mouse.move(review.x + 60, review.y + 30, { steps: 6 });
  await page.mouse.up();
  await page.locator(".savebar button.primary").click();
  await page.waitForSelector(".pill.saved");
  const stored = await (await api(`/api/projects/${PROJECT}/factories/main`)).json();
  check(
    "the loop belt heals onto the factory node's loop entry",
    stored.edges.find((edge) => edge.id === "e4")?.target_handle,
    "loop",
  );
  check(
    "…and the belt in onto its input entry",
    stored.edges.find((edge) => edge.id === "e1")?.target_handle,
    "forward",
  );

  // ---- a run goes in, and comes out ------------------------------------------
  await page.locator(".runbox-head button", { hasText: "Run" }).click();
  await page.waitForSelector(".request textarea", { timeout: 10000 });
  await page.locator(".request textarea").fill("tidy the readme");
  await page.locator(".request button[type=submit]").click();

  const status = () => page.locator(".runbox").getAttribute("data-run-status");
  const deadline = Date.now() + 40000;
  let reached = await status();
  while (!["succeeded", "failed", "cancelled", "stopped"].includes(reached ?? "")) {
    if (Date.now() > deadline) throw new Error(`the run never finished — it is ${reached}`);
    await page.waitForTimeout(250);
    reached = await status();
  }
  check("the run succeeds", reached, "succeeded");
  check(
    "the factory node lights up as the machines inside it",
    await page.locator(".node.type-factory").getAttribute("data-status"),
    "succeeded",
  );
  check("the project canvas draws none of those machines", await page.locator(".node.type-agent").count(), 1);

  // ---- and inside it -----------------------------------------------------------
  await page.locator(".node.type-factory .factory-open").click();
  await page.waitForSelector(".drill-overlay .node.connector");
  check("opening it goes inside", (await page.evaluate(() => window.location.hash)).includes("/f/n-block"), true);
  check(
    "the machine inside ran",
    await page.locator('.drill-overlay .node.type-agent').getAttribute("data-status"),
    "succeeded",
  );
  check(
    "…and its input exit passed the work out",
    await page.locator(".drill-overlay .node.connector-exit.kind-forward").getAttribute("data-status"),
    "succeeded",
  );
  check(
    "the loopback entry nothing came back through stayed dark",
    await page.locator(".drill-overlay .node.connector-entry.kind-loop").getAttribute("data-status"),
    "pending",
  );
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  check("Escape comes back out", await page.locator(".drill-overlay").count(), 0);

  // ---- a second of the same factory is numbered ----------------------------------
  //
  // The memories inside a factory node are filed under its label, so two on one
  // name would file into one document. Left unsaved: this is the last thing the
  // page does.
  await page.locator('.palette-group-trigger[data-group="factories"]').click();
  await page.locator(`.palette-item[data-kind="${FACTORY}"]`).click();
  await page.locator(`.palette-item[data-kind="${FACTORY}"]`).click();
  await page.keyboard.press("Escape");
  const labels = await page
    .locator(".node.type-factory .node-title")
    .evaluateAll((els) => els.map((el) => el.value).sort());
  check("a second factory node of one factory is numbered", labels, ["Block", `Block ${STAMP}`, `Block ${STAMP} 2`]);
} finally {
  await browser.close();
  const removedProject = await api(`/api/projects/${PROJECT}`, { method: "DELETE" });
  check("the throwaway project deletes cleanly", removedProject.status, 204);
  const removedFactory = await api(`/api/factories/${FACTORY}`, { method: "DELETE" });
  check("…and then its factory, which nothing uses any more", removedFactory.status, 204);
  console.log(`\n${passed} checks passed`);
}
