/**
 * The three ways into a machine, in a real browser.
 *
 * An agent and a human node used to draw **one unnamed target handle**, so a
 * read wire from a memory, a belt running down the line and a reviewer's fail
 * belt coming back round all arrived at the same 10px dot. Which of the three a
 * line was had to be traced by eye across the whole sheet. They are three named
 * entries now — `store`, `forward`, `loop`, top to bottom — and the side of a
 * node says what reaches it.
 *
 * What this checks is the half the backend cannot see, and it is the half that
 * matters, because **the server never reads `Edge.target_handle`**: every
 * routing decision is taken off `source_handle` (`runcheck`,
 * `orchestrator._carries`). So there is no run to assert against here. What
 * there is:
 *
 * - that the entries are drawn, and that a human node draws the two it can use;
 * - that the legal entry **lights up** while a wire is in the air, rather than
 *   only refusing the drop once the operator has found the wrong one;
 * - that the wrong drop is refused;
 * - that what is drawn is what lands in the saved file.
 *
 * `gate.mjs` carries the companion assertion — that a file saved before the
 * entries existed still draws every one of its edges — and `loop.mjs` the one
 * about a return belt finding the loop entry on its own.
 *
 * Run it:  docker compose --profile uitest run --rm uitest node tests/entries.mjs
 *
 * Same bargain as the rest: its own throwaway project, deleted at the end, and
 * it asserts rather than prints.
 */

import { chromium } from "playwright";

const BASE = process.env.FACTOR_IA_URL ?? "http://127.0.0.1:8000";
const PROJECT = `uitest-entries-${Date.now().toString(36)}`;

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
 * A line with a machine, a gate and an unwired memory beside them.
 *
 * The memory is deliberately left **unattached**: wiring it is what this test
 * does with the mouse, which is the only way to find out whether the canvas
 * refuses the wrong entry as the operator draws rather than afterwards, in a
 * list beside the Run button.
 */
const graph = {
  nodes: [
    { id: "n-in", definition: { type: "input", label: "Request", position: { x: 0, y: 200 }, config: { kind: "request" } } },
    { id: "n-agent", definition: { type: "agent", label: "Build it", codebase_id: codebase, position: { x: 340, y: 200 }, config: { kind: "worker", adapter: "fake" } } },
    { id: "n-gate", definition: { type: "human", label: "Sign-off", position: { x: 700, y: 200 }, config: { kind: "approval", prompt: "Does this look right?" } } },
    { id: "n-out", definition: { type: "output", label: "Commit", position: { x: 1060, y: 200 }, config: { kind: "commit" } } },
    { id: "n-mem", definition: { type: "memory", label: "Notes", position: { x: 340, y: 560 }, config: {} } },
  ],
  edges: [
    { id: "e1", source: "n-in", target: "n-agent" },
    { id: "e2", source: "n-agent", target: "n-gate", source_handle: "success" },
    { id: "e3", source: "n-gate", target: "n-out", source_handle: "success" },
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

  /** The centre of one node's handle, in page coordinates. */
  const at = async (node, handle) => {
    const box = await page.locator(`${node} .react-flow__handle[data-handleid="${handle}"]`).boundingBox();
    if (box === null) throw new Error(`no ${handle} handle on ${node}`);
    return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  };
  /** The same, for the one handle on a node that has no name. */
  const atUnnamed = async (node, side) => {
    const box = await page.locator(`${node} .react-flow__handle-${side}`).first().boundingBox();
    if (box === null) throw new Error(`no ${side} handle on ${node}`);
    return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  };

  // ---- what is drawn ------------------------------------------------------
  const entries = (selector) =>
    page
      .locator(selector)
      .locator(".react-flow__handle-left")
      .evaluateAll((els) => els.map((el) => el.getAttribute("data-handleid")).sort());
  check("an agent draws all three entries", await entries(".node.type-agent"), ["forward", "loop", "store"]);
  // Two, not three, and not a third that refuses everything dropped on it. Only
  // an agent may read a memory or a base, and this canvas says read-only by
  // leaving the drop target out — the same mechanism a `principles` base uses.
  check("a human node draws the two it can use", await entries(".node.type-human"), ["forward", "loop"]);
  check("storage keeps its own single way in", await entries(".node.type-memory"), [null]);

  // ---- the legal entry lights while the wire is in the air -----------------
  //
  // The point of the whole affordance. React Flow's own `valid` class lands on
  // the handle *under the pointer*, which can only tell an operator they have
  // already found the wrong one; this lights from the moment the drag starts.
  const memoryOut = await atUnnamed(".node.type-memory", "right");
  const agentStore = await at(".node.type-agent", "store");
  const agentForward = await at(".node.type-agent", "forward");

  await page.mouse.move(memoryOut.x, memoryOut.y);
  await page.mouse.down();
  await page.mouse.move((memoryOut.x + agentStore.x) / 2, (memoryOut.y + agentStore.y) / 2, { steps: 8 });
  check(
    "dragging a wire out of a memory lights the storage entry",
    await page.locator(".node.type-agent .handle-store.entry-legal").count(),
    1,
  );
  check(
    "…and only that one",
    await page.locator(".node.type-agent .react-flow__handle-left.entry-legal").count(),
    1,
  );
  check(
    "…and nothing on the gate, which may not read one",
    await page.locator(".node.type-human .entry-legal").count(),
    0,
  );
  await page.mouse.move(agentStore.x, agentStore.y, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(200);
  check("the wire lands", await page.locator(".react-flow__edge").count(), graph.edges.length + 1);
  check("…and nothing is lit once the pointer is up", await page.locator(".entry-legal").count(), 0);

  // ---- and the wrong entry is refused --------------------------------------
  //
  // A wire has exactly one place to land. Letting one sit on `forward` would put
  // a dashed attachment in the slot that means "this is what runs next".
  const before = await page.locator(".react-flow__edge").count();
  await page.mouse.move(memoryOut.x, memoryOut.y);
  await page.mouse.down();
  await page.mouse.move(agentForward.x, agentForward.y, { steps: 12 });
  await page.mouse.up();
  await page.waitForTimeout(200);
  check("a wire dropped on the forward entry is refused", await page.locator(".react-flow__edge").count(), before);

  // ---- what was drawn is what is saved -------------------------------------
  await page.locator(".savebar button.primary").click();
  await page.waitForSelector(".pill.saved");
  const stored = await (await api(`/api/projects/${PROJECT}/factories/main`)).json();
  const wire = stored.edges.find((edge) => edge.source === "n-mem" && edge.target === "n-agent");
  check("the wire the mouse drew is in the file", wire !== undefined, true);
  check("…naming the entry it was dropped on", wire?.target_handle, "store");
  // The belts that were already there heal onto their entries on the same save,
  // without the operator having redrawn any of them.
  const entryOf = (id) => stored.edges.find((edge) => edge.id === id)?.target_handle ?? null;
  check("the belt into the agent heals onto the forward entry", entryOf("e1"), "forward");
  check("…and so does the one into the gate", entryOf("e2"), "forward");
  check("…while the belt into the output node is left alone", entryOf("e3"), null);

  // ---- and it comes back as what it is -------------------------------------
  //
  // Drawn, an edge is a belt whatever it touches — `onConnect` types every new
  // one `belt`, and the wire/belt split is recomputed as a file is read
  // (`graph.isWire`, mirroring `models.split_edges`). So the reload is not
  // ceremony: it is where a wire first renders as a wire, and the only place
  // this test can see that the entry it was dropped on survives a round trip
  // through the file rather than only living in the canvas that drew it.
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".node.type-memory");
  check("the drawn wire reads back as a wire", await page.locator("path.wire").count(), 1);
  check("…and every edge is still drawn", await page.locator(".react-flow__edge").count(), stored.edges.length);
  check("…with nothing left to save", await page.locator(".pill.saved").count(), 1);
} finally {
  await browser.close();
  const removed = await api(`/api/projects/${PROJECT}`, { method: "DELETE" });
  check("the throwaway project deletes cleanly", removed.status, 204);
  console.log(`\n${passed} checks passed`);
}
