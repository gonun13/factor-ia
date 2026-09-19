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
    "…and gets no card on the timeline, being no machine of the run's",
    await page.locator(".timeline-card .who", { hasText: /^Timeline$/ }).count(),
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
  // ---- the library's editor ----------------------------------------------
  // Worth doing here rather than in a script of its own: this base has a
  // *filed* entry now, which no HTTP call could have created, and editing
  // what a machine wrote is the whole reason the editor exists.
  await page.goto(`${BASE}/#/`, { waitUntil: "networkidle" });
  await page.waitForSelector(".knowledge-section");
  await page.locator(`.knowledge-card:has-text("${TIMELINE}")`).click();
  await page.waitForSelector(".knowledge-editor");
  check("a base card opens its files", await page.locator(".knowledge-editor").count(), 1);

  const rows = page.locator(".knowledge-editor-files .file-row");
  check(
    "the manifest is the first row, above the content",
    (await rows.first().innerText()).includes("manifest.md"),
    true,
  );
  // The filing pass writes `<project>/<machine>.md`, so this base has exactly
  // one folder — said once above its files rather than at the front of each.
  // The list arrives on its own fetch, so wait for the base rather than for
  // the window: until it lands there is only the manifest to show.
  await page.waitForSelector(".knowledge-editor-files .file-group");
  check(
    "a nested entry shows its folder once, as a heading",
    await page.locator(`.knowledge-editor-files .file-folder:has-text("${PROJECT}")`).count(),
    1,
  );
  check(
    "…and the row itself carries only the filename",
    (await rows.nth(1).locator(".mono").innerText()).includes("/"),
    false,
  );
  check(
    "…with the whole path a hover away",
    (await rows.nth(1).getAttribute("title")).includes(`${PROJECT}/`),
    true,
  );
  // The point of all of that: nothing scrolls sideways.
  check(
    "the file list never scrolls sideways",
    await page.locator(".knowledge-editor-files").evaluate(
      (el) => el.scrollWidth <= el.clientWidth,
    ),
    true,
  );
  await rows.first().click();
  await page.waitForSelector(".knowledge-editor-file textarea");
  check(
    "…and it is read-only: it is what the base *is*",
    await page.locator(".knowledge-editor-file textarea").getAttribute("readonly"),
    "",
  );
  // Scoped to the editor's own footer: a card whose title happens to contain
  // the word would otherwise make this ambiguous.
  const saveButton = page.locator(".knowledge-editor .skill-editor-foot button.primary");
  check("…so Save is not offered for it", await saveButton.isDisabled(), true);

  await rows.nth(1).click();
  await page.waitForSelector(".knowledge-editor-file textarea:not([readonly])");
  const box = page.locator(".knowledge-editor-file textarea");
  const before = await box.inputValue();
  await box.fill(`${before}\n\nEdited by the operator.\n`);
  await saveButton.click();
  // The *unsaved* chip going, not Save going grey: Save is disabled for the
  // whole of the request too, so waiting on it would read the state
  // mid-flight. The chip clears only once the editor has re-baselined on what
  // the server wrote back.
  await page
    .locator(".knowledge-editor .eyebrow", { hasText: /^unsaved$/i })
    .waitFor({ state: "detached", timeout: 15_000 });
  check("saving leaves no error behind", await page.locator(".knowledge-editor .error").count(), 0);

  await page.keyboard.press("Escape");
  await page.waitForSelector(".knowledge-editor", { state: "detached" });
  await page.locator(`.knowledge-card:has-text("${TIMELINE}")`).click();
  await page.waitForSelector(".knowledge-editor");
  await page.locator(".knowledge-editor-files .file-row").nth(1).click();
  await page.waitForSelector(".knowledge-editor-file textarea");
  check(
    "an edit round-trips through the file on disk",
    (await page.locator(".knowledge-editor-file textarea").inputValue()).includes(
      "Edited by the operator.",
    ),
    true,
  );
  check(
    "the document wraps rather than scrolling sideways",
    await page.locator(".knowledge-editor-file textarea").evaluate(
      (el) => el.scrollWidth <= el.clientWidth,
    ),
    true,
  );

  // ---- deleting one file --------------------------------------------------
  // The manifest is not content and cannot go: deleting it would not empty a
  // base, it would stop the directory being one.
  check(
    "the manifest row carries no delete: it is not content",
    await page.locator(".knowledge-editor-files .card-delete").count(),
    1,
  );
  check(
    "…and the one that does belongs to the file beside it",
    await page.locator(".knowledge-editor-files .card-delete").getAttribute("aria-label"),
    `Delete ${PROJECT}/worker.md`,
  );

  check(
    "…and it is drawn as a bin, not a dismissing cross",
    await page.locator(".knowledge-editor-files .card-delete svg").count(),
    1,
  );

  await page.locator(".knowledge-editor-files .card-delete").click();
  check(
    "deleting asks in the row, where the file is",
    (await page.locator(".knowledge-editor-files .file-confirm").innerText()).includes(
      "Delete worker.md?",
    ),
    true,
  );
  check(
    "…and the row it replaced is gone while it asks",
    await page.locator(".knowledge-editor-files .file-row").count(),
    1,
  );
  check(
    "…though the file is still there until the question is answered",
    (await (await api(`/api/knowledge/${TIMELINE}`)).json()).files,
    1,
  );
  // Cancel puts the row back, unharmed: a confirm you cannot back out of is a
  // confirm that only slows the mistake down.
  await page.locator(".knowledge-editor-files .file-confirm .link-button").click();
  check(
    "…and cancelling restores it",
    await page.locator(".knowledge-editor-files .file-row").count(),
    2,
  );

  await page.locator(".knowledge-editor-files .card-delete").click();
  await page.locator(".knowledge-editor-files .file-confirm button.destructive").click();
  // The confirm going is the signal, not the row going: the row is replaced
  // the moment the question opens, so waiting on it would measure the click
  // rather than the delete — and would then race the request that follows.
  await page
    .locator(".knowledge-editor-files .file-confirm")
    .waitFor({ state: "detached", timeout: 15_000 });
  check(
    "…and the base is left with nothing but its manifest",
    await page.locator(".knowledge-editor-files .file-slot").count(),
    0,
  );
  check(
    "…which the list says in words rather than leaving blank",
    (await page.locator(".knowledge-editor-files").innerText()).includes("Nothing filed yet"),
    true,
  );
  check(
    "…with nothing opened in its place",
    await page.locator(".knowledge-editor-file textarea").count(),
    0,
  );
  const emptied = await (await api(`/api/knowledge/${TIMELINE}`)).json();
  check("…and the file really is gone from the base", emptied.files, 0);

  await page.keyboard.press("Escape");
  await page.waitForSelector(".knowledge-editor", { state: "detached" });
  check(
    "the card's count follows the delete without a reload",
    (await page.locator(`.knowledge-card:has-text("${TIMELINE}") .meta`).innerText()).includes(
      "0 files",
    ),
    true,
  );

  // The card is a button now and the delete is its sibling. If that nesting
  // ever goes back to a button inside a button, this is what catches it.
  await page.locator(`.skill-slot:has(.knowledge-card:has-text("${TIMELINE}")) .card-delete`).click();
  check(
    "the delete × still confirms rather than opening the editor",
    await page.locator(".knowledge-editor").count(),
    0,
  );
  check(
    "…and the confirm is the one that appears",
    await page.locator('.skill-card.is-confirming:has-text("Delete")').count(),
    1,
  );
  await page.locator(".skill-card.is-confirming button.link-button").click();
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
