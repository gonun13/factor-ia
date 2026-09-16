/**
 * The knowledge node, in a real browser.
 *
 * What the backend tests cannot see: that Knowledge is in the palette beside
 * Memory, that a base has a handle on each side so the *direction* of a wire is
 * the permission, that the write handle — the target one — *disappears* on a
 * principles base and takes its wires with it rather than leaving edges React
 * Flow silently drops, and that the library on the projects screen creates and
 * deletes a real directory.
 *
 * Run it:  docker compose --profile uitest run --rm uitest node tests/knowledge.mjs
 *
 * Same bargain as `memory.mjs`: its own throwaway project and its own throwaway
 * bases, deleted at the end, and it asserts rather than prints.
 */

import { chromium } from "playwright";

const BASE = process.env.FACTOR_IA_URL ?? "http://127.0.0.1:8000";
const STAMP = Date.now().toString(36);
const PROJECT = `uitest-knowledge-${STAMP}`;
const TIMELINE = `uitest-timeline-${STAMP}`;
const RULES = `uitest-rules-${STAMP}`;

let passed = 0;
function check(label, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `\n        expected ${JSON.stringify(expected)}\n        actual   ${JSON.stringify(actual)}`}`);
  if (!ok) process.exitCode = 1;
  else passed += 1;
}

async function openPalette(page, group) {
  const trigger = page.locator(`.palette-group-trigger[data-group="${group}"]`);
  if ((await trigger.getAttribute("aria-expanded")) !== "true") await trigger.click();
  return page.locator(`#palette-menu-${group}`);
}

const api = (path, init) =>
  fetch(`${BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", Origin: BASE, ...init?.headers },
  });

const codebases = await (await api("/api/codebases")).json();
if (codebases.length === 0) throw new Error("no codebase under /workspace to attach");
const codebase = codebases[0].id;

// Two bases: one a machine may write to, and one no machine may ever write to.
// The pair is the point — every rule below is about the difference between them.
for (const [id, scope, about] of [
  [TIMELINE, "codebase", "Every change we have made to this repository, in order."],
  [RULES, "principles", "How we build software here; naming, review standards."],
]) {
  const made = await api("/api/knowledge", {
    method: "POST",
    body: JSON.stringify({ id, title: id, scope, about }),
  });
  if (!made.ok) throw new Error(`could not create ${id}: ${await made.text()}`);
}

const created = await api("/api/projects", {
  method: "POST",
  body: JSON.stringify({ name: PROJECT, id: PROJECT, codebase_ids: [codebase] }),
});
if (!created.ok) throw new Error(`could not create the test project: ${await created.text()}`);

/**
 * The canonical shape: the line, with two bases hanging off it.
 *
 *   Request ──► Worker ──► Commit
 *                 │  ╰┈write┈► [timeline]
 *                 ╰──◄┈read┈┈┈ [timeline]
 *                 ╰──◄┈read┈┈┈ [rules]
 *
 * **Direction is the permission**, exactly as it is for a memory: a wire into
 * the base contributes, a wire out of it consults. The Worker both reads and
 * writes the timeline, which draws as a round trip — the honest picture of work
 * going in and coming out — and reads the principles base, which nothing may
 * ever write.
 */
const graph = {
  nodes: [
    { id: "n-in", definition: { type: "input", label: "Request", position: { x: 0, y: 160 }, config: { kind: "request" } } },
    { id: "n-do", definition: { type: "agent", label: "Worker", codebase_id: codebase, position: { x: 380, y: 160 }, config: { kind: "worker", adapter: "fake" } } },
    { id: "n-out", definition: { type: "output", label: "Commit", position: { x: 760, y: 160 }, config: { kind: "commit" } } },
    { id: "n-base", definition: { type: "knowledge", label: "Timeline", position: { x: 0, y: 400 }, config: { base: TIMELINE } } },
    { id: "n-rules", definition: { type: "knowledge", label: "Principles", position: { x: 0, y: 620 }, config: { base: RULES } } },
  ],
  edges: [
    { id: "e1", source: "n-in", target: "n-do" },
    { id: "e2", source: "n-do", target: "n-out" },
    // Wires. The direction is the permission; the handle name is only how
    // React Flow addresses the dot.
    { id: "w1", source: "n-base", target: "n-do", source_handle: "read" },
    { id: "w2", source: "n-do", target: "n-base", target_handle: "write" },
    { id: "w3", source: "n-rules", target: "n-do", source_handle: "read" },
  ],
};
const saved = await api(`/api/projects/${PROJECT}/factories/main`, {
  method: "PUT",
  body: JSON.stringify(graph),
});
if (!saved.ok) throw new Error(`could not save the graph: ${await saved.text()}`);

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  page.on("pageerror", (error) => {
    console.log(`FAIL  uncaught page error: ${error.message}`);
    process.exitCode = 1;
  });

  // ---- the library on the projects screen ---------------------------------
  await page.goto(`${BASE}/#/`, { waitUntil: "networkidle" });
  await page.waitForSelector(".knowledge-section");
  check(
    "both bases are in the library",
    await page.locator(`.knowledge-card:has-text("${TIMELINE}"), .knowledge-card:has-text("${RULES}")`).count(),
    2,
  );
  check(
    "a principles base says it is read-only to machines",
    (await page.locator(`.knowledge-card:has-text("${RULES}") .meta`).innerText()).includes(
      "read-only to machines",
    ),
    true,
  );
  check(
    "…and a writable one does not",
    (await page.locator(`.knowledge-card:has-text("${TIMELINE}") .meta`).innerText()).includes(
      "read-only",
    ),
    false,
  );

  // ---- the machine --------------------------------------------------------
  await page.goto(`${BASE}/#/p/${PROJECT}`, { waitUntil: "networkidle" });
  await page.waitForSelector(".node.type-knowledge");

  check(
    "Knowledge is in the palette, under Storage beside Memory",
    await (await openPalette(page, "storage"))
      .locator(".palette-item", { hasText: "Knowledge" })
      .count(),
    1,
  );
  await page.locator('.palette-group-trigger[data-group="storage"]').click();

  check(
    "the node says what it is",
    (await page.locator('.react-flow__node[data-id="n-base"] .eyebrow').innerText()).toLowerCase(),
    "knowledge",
  );
  check("both bases are on the canvas", await page.locator(".node.type-knowledge").count(), 2);

  // **Its one piece of configuration**, and the difference from a memory, which
  // has none: a base is a reference to something that already exists and
  // already has a name, so the label is free text and the id is the fact.
  check(
    "a knowledge node selects a base",
    await page.locator('.react-flow__node[data-id="n-base"] .slot.base select').inputValue(),
    TIMELINE,
  );

  // **A handle on each side, because direction is the permission.** `write` is
  // the target handle on the left, where a contributing machine's wire arrives;
  // `read` is the source handle on the right. A reviewer of a factory reads the
  // permission off the arrow rather than off a colour.
  const timelineNode = page.locator('.react-flow__node[data-id="n-base"]');
  check(
    "a writable base takes a wire in and gives one out",
    await timelineNode.locator(".react-flow__handle.handle-write").count() +
      await timelineNode.locator(".react-flow__handle.handle-read").count(),
    2,
  );
  // **And a principles base has nowhere for a write wire to land.** That is the
  // whole mechanism behind read-only on the canvas: not a rule somebody has to
  // read, but the absence of a drop target.
  const rulesNode = page.locator('.react-flow__node[data-id="n-rules"]');
  check(
    "a principles base still gives a read wire out",
    await rulesNode.locator(".react-flow__handle.handle-read").count(),
    1,
  );
  check(
    "…and has no write handle for one to arrive at",
    await rulesNode.locator(".react-flow__handle.handle-write").count(),
    0,
  );

  check(
    "a writable base's only target handle is its write handle",
    await timelineNode.locator(".react-flow__handle[data-handlepos='left']").count(),
    1,
  );

  // Wires, not belts: nothing travels them and nothing waits on them. And
  // drawn exactly as a memory's wires are — both attach storage and decide
  // nothing, so the canvas must not imply the two are different categories.
  check("the three wires are drawn as wires", await page.locator("path.wire").count(), 3);
  check("the two belts are still belts", await page.locator(".belt-casing").count(), 2);
  check(
    "a base wears storage's colour, not one of its own",
    await page.evaluate(() => {
      // Resolved through a probe rather than read off `--knowledge`, which is
      // an alias and comes back as the literal text "var(--memory)". Both sides
      // have to be computed values or this compares a declaration to a colour.
      const probe = document.createElement("span");
      probe.style.color = "var(--memory)";
      document.body.appendChild(probe);
      const storage = getComputedStyle(probe).color;
      probe.remove();
      const base = document.querySelector(".node.type-knowledge");
      return getComputedStyle(base).color === storage;
    }),
    true,
  );

  check("nothing blocks this factory", await page.locator(".problem.blocker").count(), 0);

  // **A reader with no writer above it is the normal case**, which is the one
  // memory rule that deliberately does not carry over: the principles base is
  // read by a machine and written by nobody, and that is what it is *for*.
  check(
    "reading a base nothing in this factory writes is not a problem",
    await page.locator("text=/which nothing writes to/").count(),
    0,
  );

  // ---- the handle that disappears, and what happens to its wires ----------
  //
  // React Flow *silently drops* an edge whose `sourceHandle` names a handle its
  // source node does not render — not a warning, not a broken line, simply not
  // there while the file still carries it. So switching a node onto a
  // principles base must take the write wires away **visibly**.
  await timelineNode.locator(".slot.base select").selectOption(RULES);
  await page.waitForSelector(".notice");
  check(
    "switching to a principles base says the write wire was removed",
    (await page.locator(".notice").innerText()).includes("read-only to every machine"),
    true,
  );
  check("…and the wire really is gone", await page.locator("path.wire").count(), 2);
  check(
    "…and no node draws a write handle any more",
    await page.locator(".react-flow__handle.handle-write").count(),
    0,
  );

  // Back to the saved graph: that change was never saved.
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".node.type-knowledge");
  await page.waitForTimeout(500);
  check("reloading restores the write wire", await page.locator("path.wire").count(), 3);

  // ---- who may be on the other end ---------------------------------------
  //
  // Direction decides *what* a wire means, and there is no wrong direction any
  // more — but there is still a wrong node. An input node wired into a base
  // would fill it with text no contract ever shaped.
  const wrong = {
    ...graph,
    edges: [...graph.edges, { id: "w4", source: "n-in", target: "n-base", target_handle: "write" }],
  };
  await api(`/api/projects/${PROJECT}/factories/main`, {
    method: "PUT",
    body: JSON.stringify(wrong),
  });
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".node.type-knowledge");
  await page.waitForTimeout(500);
  check(
    "only an agent may write to a base",
    (await page.locator("text=/only an agent may write to one/").count()) > 0,
    true,
  );

  await api(`/api/projects/${PROJECT}/factories/main`, {
    method: "PUT",
    body: JSON.stringify(graph),
  });
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".node.type-knowledge");
  await page.waitForTimeout(500);
  check("…and removing it settles the canvas", await page.locator(".problem.blocker").count(), 0);

  // ---- the run ------------------------------------------------------------
  await page.locator(".runbox button.run").click();
  await page.waitForSelector(".request textarea");
  await page.locator(".request textarea").fill("Record what changed.");
  await page.getByRole("button", { name: /Submit the request/ }).click();
  await page.waitForSelector('.runbox[data-run-status="succeeded"]', { timeout: 120_000 });

  // A base has no status, because it never runs. A pip on it would be the
  // canvas claiming storage is a station on the line.
  check(
    "a base never gets a status of its own",
    await page.locator('.react-flow__node[data-id="n-base"] .node').getAttribute("data-status"),
    "idle",
  );
  check(
    "…and gets no thread in the run log, being no machine of the run's",
    await page.locator(".thread .who", { hasText: /^Timeline$/ }).count(),
    0,
  );

  // **The claim the lifetime rests on.** A memory is discarded when its run
  // succeeds; nothing in a base is ever cleaned up. Both bases are still here,
  // with everything that was in them.
  const still = await (await api("/api/knowledge")).json();
  check(
    "both bases outlive the run",
    still.filter((one) => one.id === TIMELINE || one.id === RULES).length,
    2,
  );
} finally {
  await browser.close();
  for (const id of [TIMELINE, RULES]) {
    const removed = await api(`/api/knowledge/${id}`, { method: "DELETE" });
    check(`the throwaway base ${id} deletes cleanly`, removed.status, 204);
  }
  const removed = await api(`/api/projects/${PROJECT}`, { method: "DELETE" });
  check("the throwaway project deletes cleanly", removed.status, 204);
  console.log(`\n${passed} checks passed`);
}
