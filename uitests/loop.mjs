/**
 * The reviewer, the verdict and the loop, in a real browser.
 *
 * What the backend tests cannot see: that a reviewer is in the palette at all,
 * that the two agent kinds draw a different number of ports (which is what
 * decides whether a loop can even be *drawn*), that neither the kind nor the
 * lap cap is offered as a control, that a back-belt renders as a return conveyor
 * rather than as a smear across two machines, and that a node sent round again
 * says which lap it is on instead of quietly redrawing itself.
 *
 * It also owns the **loop entry**: this is the only fixture with a belt that
 * genuinely comes back round, so it is the only one that can check the canvas
 * tells a return belt from a forward one without being told which is which.
 *
 * Run it:  docker compose --profile uitest run --rm uitest node tests/loop.mjs
 *
 * Same bargain as `gate.mjs`: its own throwaway project, deleted at the end,
 * and it asserts rather than prints.
 */

import { chromium } from "playwright";

const BASE = process.env.FACTOR_IA_URL ?? "http://127.0.0.1:8000";
const PROJECT = `uitest-loop-${Date.now().toString(36)}`;

let passed = 0;
function check(label, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `\n        expected ${JSON.stringify(expected)}\n        actual   ${JSON.stringify(actual)}`}`);
  if (!ok) process.exitCode = 1;
  else passed += 1;
}

/**
 * Open a palette group and hand back its menu.
 *
 * The palette is a bar of collapsed groups now, so a test reaching for an entry
 * has to open the group holding it first. Idempotent on purpose: a group left
 * open is not toggled shut, which is what lets a test click the same entry
 * twice.
 */
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

const created = await api("/api/projects", {
  method: "POST",
  body: JSON.stringify({ name: PROJECT, id: PROJECT, codebase_ids: [codebase] }),
});
if (!created.ok) throw new Error(`could not create the test project: ${await created.text()}`);

// The canonical loop: work, review, and a fail belt back to the work. The
// reviewer fails on its first look and passes on its second, so the line
// genuinely turns once — `fake_scripts` is the only way to say that, because a
// node's script used to be fixed for the whole run.
const graph = {
  nodes: [
    { id: "n-in", definition: { type: "input", label: "Request", config: { kind: "request" } } },
    { id: "n-work", definition: { type: "agent", label: "Build it", codebase_id: codebase, position: { x: 300, y: 0 }, config: { kind: "worker", adapter: "fake" } } },
    { id: "n-review", definition: { type: "agent", label: "Review", codebase_id: codebase, position: { x: 640, y: 0 }, config: { kind: "reviewer", adapter: "fake", max_laps: 3, fake_scripts: ["verdict-fail", "verdict-pass"] } } },
    { id: "n-out", definition: { type: "output", label: "Commit", position: { x: 980, y: 0 }, config: { kind: "commit" } } },
  ],
  edges: [
    { id: "e1", source: "n-in", target: "n-work" },
    { id: "e2", source: "n-work", target: "n-review" },
    { id: "e3", source: "n-review", target: "n-out", source_handle: "success" },
    { id: "e4", source: "n-review", target: "n-work", source_handle: "fail" },
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
  await page.waitForSelector(".node.type-agent");

  // ---- the palette --------------------------------------------------------
  const agentsMenu = await openPalette(page, "agents");
  check(
    "the reviewer is its own palette entry, not a kind picked on a placed agent",
    await agentsMenu.locator(".palette-item", { hasText: "Reviewer" }).count(),
    1,
  );
  // The same invariant one category over, where it costs the most: filing the
  // palette must stay a filing decision, never a kind picker. Three commits are
  // three things you choose between before placing anything — a single "Output"
  // configured afterwards is how the merging one gets picked by accident.
  const outputMenu = await openPalette(page, "output");
  check(
    "the commit kinds are three separate entries",
    await outputMenu
      .locator(".palette-item .label")
      .evaluateAll((els) => els.map((el) => el.textContent).sort()),
    ["Commit", "Commit & merge", "Commit & push"],
  );

  // ---- the ports, which are what make a loop drawable ---------------------
  const ports = (selector) =>
    page
      .locator(selector)
      .locator(".react-flow__handle-right")
      .evaluateAll((els) => els.map((el) => el.getAttribute("data-handleid")).sort());
  check("a worker has one unnamed way out", await ports(".node.kind-worker"), [null]);
  check("a reviewer draws both ports", await ports(".node.kind-reviewer"), ["fail", "success"]);
  check(
    "…and says which kind it is",
    (await page.locator(".node.kind-reviewer .eyebrow").innerText()).toLowerCase(),
    "reviewer",
  );

  // ---- a belt naming a port its source no longer has ----------------------
  //
  // React Flow drops such an edge silently — no warning, no broken line, just
  // a belt the server still honours and the canvas will not draw, which is a
  // file you cannot fix because you cannot see what to delete. Every agent drew
  // `success`/`fail` handles before agents had kinds, so every belt out of what
  // is now a worker is one of these. `toFlowEdge` moves it onto the node's
  // single way out; this is the check that it is on screen at all.
  const stale = await api(`/api/projects/${PROJECT}/factories/main`);
  const withStale = await stale.json();
  withStale.edges = withStale.edges.map((edge) =>
    edge.id === "e2b" ? edge : edge.source === "n-work" && edge.target === "n-review"
      ? { ...edge, source_handle: "success" }  // the shape a pre-kinds file has
      : edge,
  );
  await api(`/api/projects/${PROJECT}/factories/main`, {
    method: "PUT",
    body: JSON.stringify({ nodes: withStale.nodes, edges: withStale.edges }),
  });
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".node.kind-reviewer");
  await page.waitForTimeout(800);
  check(
    "a belt naming a port its worker no longer has is still drawn",
    await page.locator(".react-flow__edge").count(),
    withStale.edges.length,
  );

  // ---- the back belt ------------------------------------------------------
  check("the belt that loops back is drawn as one", await page.locator(".belt-back").count(), 1);
  check("…and still as a fail belt", await page.locator(".belt-fail.belt-back").count(), 1);

  // ---- what the node does NOT offer ---------------------------------------
  //
  // The kind is decided when the machine is dropped and is not a control on it:
  // changing it would move the node's ports out from under belts already drawn
  // from them. The lap cap is read from the file and has no plate yet. Both are
  // asserted as absences because both were briefly present, and a control that
  // creeps back would otherwise creep back silently.
  check(
    "the kind is shown, not offered as a control",
    await page.locator('.node.kind-reviewer select[aria-label="agent kind"]').count(),
    0,
  );
  check(
    "the lap cap is internal for now",
    await page.locator('.node.kind-reviewer input[aria-label="maximum laps"]').count(),
    0,
  );
  const stored = await (await api(`/api/projects/${PROJECT}/factories/main`)).json();
  const reviewer = stored.nodes.find((node) => node.id === "n-review");
  check("…and still read from the factory file", reviewer.definition.config.max_laps, 3);

  // ---- where a return belt arrives ----------------------------------------
  //
  // The target-side half of the same idea as the two ports: a return belt used
  // to land on the same dot a forward belt did, so which was which could only be
  // settled by tracing the line. The fixture above saved **no `target_handle` at
  // all**, so this is checking that the canvas worked out for itself which belt
  // comes back round — the question `runcheck.back_edges` answers server-side —
  // and put it on the loop entry.
  //
  // Asserted through a save rather than off the DOM, because the claim is about
  // two things at once: what the canvas drew, and that the **file heals** when
  // it is next written. A factory opened and saved should come back naming its
  // entries, without the operator having redrawn anything.
  // A real edit, so the graph is genuinely dirty and Save is live. Normalising
  // the entries on open deliberately does *not* dirty it — the baseline is taken
  // after `loadGraph` — so there has to be something else to save.
  await page.locator('.node.kind-worker input.node-title').fill("Build it twice");
  await page.locator(".savebar button.primary").click();
  await page.waitForSelector(".pill.saved");
  const healed = await (await api(`/api/projects/${PROJECT}/factories/main`)).json();
  const entryOf = (id) => healed.edges.find((edge) => edge.id === id)?.target_handle ?? null;
  check("the belt that closes the loop is saved onto the loop entry", entryOf("e4"), "loop");
  check("…and the belt that runs down the line onto the forward one", entryOf("e2"), "forward");
  check("…while the belt into the output node is left alone", entryOf("e3"), null);

  // ---- the loop turns -----------------------------------------------------
  await page.locator(".runbox button.run").click();
  await page.waitForSelector(".request textarea");
  await page.locator(".request textarea").fill("Add the bounds check.");
  await page.getByRole("button", { name: /Submit the request/ }).click();

  // The worker is sent round exactly once, so it reaches lap 2 — and the chip
  // only ever appears above lap 1, which is the whole reason it is legible.
  await page.waitForSelector(".node.kind-worker .lap", { timeout: 60_000 });
  check(
    "the worker says which lap it is on",
    (await page.locator(".node.kind-worker .lap").innerText()).toLowerCase(),
    "lap 2",
  );

  await page.waitForSelector('.runbox[data-run-status="succeeded"]', { timeout: 120_000 });
  check(
    "the reviewer passed on its second look",
    await page.locator(".node.kind-reviewer").getAttribute("data-status"),
    "succeeded",
  );
  check(
    "…and the line reached its commit",
    await page.locator(".node.type-output").getAttribute("data-status"),
    "succeeded",
  );
  check(
    "the finished worker is green, not still showing a lap in flight",
    await page.locator(".node.kind-worker").getAttribute("data-status"),
    "succeeded",
  );
} finally {
  await browser.close();
  const removed = await api(`/api/projects/${PROJECT}`, { method: "DELETE" });
  check("the throwaway project deletes cleanly", removed.status, 204);
  console.log(`\n${passed} checks passed`);
}
