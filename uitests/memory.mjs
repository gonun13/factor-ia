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

/**
 * The canonical shape: the line, with a memory attached to it.
 *
 *   Request ──► Research ──► Planner ──► Commit
 *                  ╰┈┈► [Codebase index] ┈┈╯
 *
 * The two dotted connections are **wires**, not belts. Take the memory away and
 * the line is unchanged. The reader runs the `echo` script, which says its
 * prompt back, so what it was *handed* is assertable on screen rather than only
 * in a run record.
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
    { id: "e2", source: "n-research", target: "n-plan" },
    { id: "e3", source: "n-plan", target: "n-out" },
    // Wires.
    { id: "w1", source: "n-research", target: "n-mem" },
    { id: "w2", source: "n-mem", target: "n-plan" },
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
    "Memory is in the palette, under Storage",
    await (await openPalette(page, "storage"))
      .locator(".palette-item", { hasText: "Memory" })
      .count(),
    1,
  );
  check(
    "the node says what it is",
    (await page.locator(".node.type-memory .eyebrow").innerText()).toLowerCase(),
    "memory",
  );

  // **What attaches it, drawn as what it is.** A wire is not a belt: nothing
  // travels it and nothing waits on it, so it gets none of a belt's casing or
  // tread. If these ever render the same, the canvas is telling an operator
  // that storage drives the line.
  check("the two wires are drawn as wires", await page.locator("path.wire").count(), 2);
  check(
    "…and none of them is drawn as a belt",
    await page.locator(".react-flow__edge:has(path.wire) .belt-casing").count(),
    0,
  );
  check("the three belts are still belts", await page.locator(".belt-casing").count(), 3);

  // **One name, and it is the one on the chassis.** A memory has no
  // configuration: the label is the filename and is what the reader sees in
  // the pointer, so a second field here would only be a way for the name a
  // person reads and the name on disk to disagree. The run assertion at the
  // bottom of this file is what ties this label to `codebase-index` on disk.
  check(
    "the label is the whole of it",
    await page.locator(".node.type-memory .node-title").inputValue(),
    "Codebase index",
  );
  check("it has nothing to configure", await page.locator(".node.type-memory .slot").count(), 0);

  // **Empty is a thing to say, not a thing to leave out.** A node that only
  // ever spoke up when it held something would leave a reader deciding between
  // "nothing was filed" and "the canvas does not show that" — and the second
  // reading is what sends someone to the filesystem to find out.
  check(
    "a memory nothing has been filed in says so",
    await page.locator(".node.type-memory .filed-line").innerText(),
    "empty",
  );

  // **The names have to be unique, so the palette makes them unique.** Two
  // memories on one name are refused before a run starts; a person who dropped
  // two and met that refusal would be fixing something the canvas could have
  // never done. Dropped against "Codebase index", which is a memory but not a
  // "Memory", so the first one here is still the unnumbered one.
  // Clicking a machine deliberately leaves its category open, which is what
  // makes dropping two in a row a two-click gesture, not a four-click one.
  const storage = await openPalette(page, "storage");
  const palette = storage.locator(".palette-item", { hasText: "Memory" });
  await palette.click();
  await palette.click();
  const labels = await page.locator(".node.type-memory .node-title").evaluateAll((nodes) =>
    nodes.map((node) => node.value).sort(),
  );
  check("a second Memory is numbered past the first", labels, [
    "Codebase index",
    "Memory",
    "Memory 2",
  ]);
  check("…and the canvas is not refusing them", await page.locator(".problem.blocker").count(), 0);

  // Back to the saved graph: those two were never saved, and nothing below
  // this line expects them.
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".node.type-memory");

  check("nothing blocks this factory", await page.locator(".problem.blocker").count(), 0);

  // ---- the rule that exists because a wire carries no ordering ------------
  //
  // Wired backwards: the Planner files it and Research reads it, so the memory
  // would still be empty when Research got there. The server refuses it, but
  // finding that out on the Run button is a worse way to learn a rule than
  // seeing it as the wire is drawn.
  const backwards = {
    ...graph,
    edges: [
      ...graph.edges.filter((edge) => !edge.id.startsWith("w")),
      { id: "w1", source: "n-plan", target: "n-mem" },
      { id: "w2", source: "n-mem", target: "n-research" },
    ],
  };
  await api(`/api/projects/${PROJECT}/factories/main`, {
    method: "PUT",
    body: JSON.stringify(backwards),
  });
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".node.type-memory");
  await page.waitForTimeout(500);
  check(
    "a reader whose writer is below it on the line is refused",
    (await page.locator("text=/is not above it on the line/").count()) > 0,
    true,
  );

  await api(`/api/projects/${PROJECT}/factories/main`, {
    method: "PUT",
    body: JSON.stringify(graph),
  });
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".node.type-memory");
  await page.waitForTimeout(500);
  check("…and wiring it the right way round settles it", await page.locator(".problem.blocker").count(), 0);

  // ---- the run ------------------------------------------------------------
  await page.locator(".runbox button.run").click();
  await page.waitForSelector(".request textarea");
  await page.locator(".request textarea").fill("Index this codebase.");
  await page.getByRole("button", { name: /Submit the request/ }).click();

  await page.waitForSelector('.runbox[data-run-status="succeeded"]', { timeout: 120_000 });
  // A memory has no status, because it never runs. A pip on it would be the
  // canvas claiming storage is a station on the line.
  // **What is in it, on it.** The one question worth asking of a shelf, and
  // one a run otherwise answers only in a file nobody on this screen has a
  // path to. Shown without ever becoming a status — see the next check.
  // Shape, not the number: the size is whatever the fake writer had to say,
  // and pinning it here would make this test fail on a reworded fixture.
  check(
    "…and the same line names what was filed once something is",
    /^1 section · \d+(\.\d)? [kM]?B$/.test(
      await page.locator(".node.type-memory .filed-line").innerText(),
    ),
    true,
  );
  check(
    "…and the footer says so as storage, not as a worktree",
    await page.locator(".branchline .memory").innerText(),
    "▣ filed 1 section",
  );
  check(
    "the memory never gets a status of its own",
    await page.locator(".node.type-memory").getAttribute("data-status"),
    "idle",
  );
  check(
    "…and gets no thread in the run log, being no machine of the run's",
    await page.locator(".thread .who", { hasText: /^Codebase index$/ }).count(),
    0,
  );

  // **The claim the whole design rests on.** The reader echoes its prompt, so
  // what is on screen under the Planner is exactly what it was handed — and it
  // must be a path, not the document.
  const handed = await page.locator(".thread", { hasText: "Planner" }).innerText();
  check("the reader was told a memory exists", handed.includes("Memory available"), true);
  // And it still got its predecessor's answer down the belt, exactly as it
  // would with no memory in the factory: an attachment adds, it never replaces.
  check("…without losing what the belt brought it", handed.includes("Done."), true);
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
