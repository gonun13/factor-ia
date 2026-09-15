/**
 * The human node and the two ports, in a real browser, against the bundle an
 * operator actually loads.
 *
 * What it is checking is the half of stage 8 the backend tests cannot see: that
 * the node exists in the palette, that a node with two outcomes draws two named
 * source handles, that dragging from one lands the port in the saved file
 * (`source_handle`), and that the decision form appears in the run panel when a
 * run parks — and says what it is asking.
 *
 * The agent assertion **inverted** in stage 9: an agent node used to draw two
 * ports whatever it was for, and now only a `reviewer` does. A worker has one
 * way out, which is the whole of what "worker" means, so it is checked here
 * alongside the input node it now behaves like. `loop.mjs` covers the reviewer.
 *
 * Run it:  docker compose --profile uitest run --rm uitest node tests/gate.mjs
 *
 * Same bargain as `settings.mjs`: its own throwaway project, deleted at the end,
 * and it asserts rather than prints.
 */

import { chromium } from "playwright";

const BASE = process.env.FACTOR_IA_URL ?? "http://127.0.0.1:8000";
const PROJECT = `uitest-gate-${Date.now().toString(36)}`;

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

// Dry-run the whole factory from the project, not from each node. Every agent
// here is fake, and `default_provider` is the rung that says so once — the
// per-node `adapter` key is for the other case, one fake machine among real
// ones. Stated on the node it is invisible: two graph configs decide that a
// branch full of scripted edits is not real work, and a run that reaches an
// operator looks exactly like one that cost money.
const configured = await api(`/api/projects/${PROJECT}`, {
  method: "PATCH",
  body: JSON.stringify({ settings: { default_provider: "fake", default_model: null } }),
});
if (!configured.ok) throw new Error(`could not pin the project to the fake provider: ${await configured.text()}`);
check("the project drives its agents with the fake provider", (await configured.json()).settings.default_provider, "fake");

// A line with a gate in it, saved through the API so the browser loads a graph
// rather than draws one — what is under test here is the rendering and the
// run panel, and dragging four nodes is `settings.mjs`'s kind of work.
const graph = {
  nodes: [
    { id: "n-in", definition: { type: "input", label: "Request", config: { kind: "request" } } },
    { id: "n-agent", definition: { type: "agent", label: "Agent", codebase_id: codebase, position: { x: 260, y: 0 }, config: {} } },
    { id: "n-gate", definition: { type: "human", label: "Sign-off", position: { x: 520, y: 0 }, config: { kind: "approval", prompt: "Does this look right?" } } },
    { id: "n-fix", definition: { type: "agent", label: "Rework", codebase_id: codebase, position: { x: 760, y: 200 }, config: {} } },
    { id: "n-out", definition: { type: "output", label: "Commit", position: { x: 1020, y: 0 }, config: { kind: "commit" } } },
  ],
  edges: [
    { id: "e1", source: "n-in", target: "n-agent" },
    { id: "e2", source: "n-agent", target: "n-gate", source_handle: "success" },
    { id: "e3", source: "n-gate", target: "n-out", source_handle: "success" },
    { id: "e4", source: "n-gate", target: "n-fix", source_handle: "fail" },
    { id: "e5", source: "n-fix", target: "n-out", source_handle: "success" },
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
  await page.waitForSelector(".node.type-human");

  // ---- the node, and the palette it comes from ----------------------------
  check("the palette offers a human node", await page.getByRole("button", { name: /^Sign-off/ }).count(), 1);
  check("the human node renders with its own chassis", await page.locator(".node.type-human").count(), 1);
  check("…and says what kind it is", (await page.locator(".node.type-human .eyebrow").innerText()).toLowerCase(), "sign-off");
  check("…and carries the question as an editable field", await page.locator('.node.type-human input[aria-label="what to ask the operator"]').inputValue(), "Does this look right?");

  // ---- the ports ----------------------------------------------------------
  const ports = (selector) =>
    page
      .locator(selector)
      .first()  // this line has two agent nodes; one of them answers the question
      .locator(".react-flow__handle-right")
      .evaluateAll((els) => els.map((el) => el.getAttribute("data-handleid")).sort());
  check("a worker agent keeps one unnamed way out", await ports(".node.type-agent"), [null]);
  check("a human node draws both ports", await ports(".node.type-human"), ["fail", "success"]);
  check("an input node keeps its single unnamed way out", await ports(".node.type-input"), [null]);
  check("an output node draws none", await ports(".node.type-output"), []);
  check("the fail belt is drawn as one", await page.locator(".belt-fail").count(), 1);

  // ---- the run panel ------------------------------------------------------
  await page.locator(".runbox button.run").click();
  await page.waitForSelector(".request textarea");
  await page.locator(".request textarea").fill("Tidy the README.");
  await page.getByRole("button", { name: /Submit the request/ }).click();

  // The walk cuts a real worktree on its way here, so this is a wait, not a poll.
  await page.waitForSelector("form.decision", { timeout: 60_000 });
  check("the decision form asks the graph's own question", await page.locator("form.decision label").innerText(), "Does this look right?");
  check("…and shows what arrived, to judge", await page.locator(".decision-incoming").count(), 1);
  check("…with both ways out, neither of them buried", await page.locator(".decision-actions button").allInnerTexts(), ["Approve", "Reject"]);
  check("the gate's node is the one waiting", (await page.locator(".node.type-human .status-word").innerText()).toLowerCase(), "waiting input");

  // ---- rejecting routes, and the run still finishes -----------------------
  await page.locator("form.decision textarea").fill("wrong file — redo it");
  await page.getByRole("button", { name: "Reject" }).click();

  // The panel stamps the run's own status, so this waits for the thing being
  // asserted rather than for a paragraph that is also used for a cost readout.
  // A rejection the graph drew a belt for ends the run **succeeded** — that is
  // the whole claim, and the rework machine downstream of the fail port is
  // what has to have run for it to be true.
  await page.waitForSelector('.runbox[data-run-status="succeeded"]', { timeout: 60_000 });

  check("the rejected node is red, not hidden", await page.locator(".node.type-human").getAttribute("data-status"), "failed");
  check("the rework machine on the fail belt ran", await page.locator(".node.type-agent").last().getAttribute("data-status"), "succeeded");
  check("…and the line still reached its commit", await page.locator(".node.type-output").getAttribute("data-status"), "succeeded");
  check("the form is gone once it is answered", await page.locator("form.decision").count(), 0);
} finally {
  await browser.close();
  const removed = await api(`/api/projects/${PROJECT}`, { method: "DELETE" });
  check("the throwaway project deletes cleanly", removed.status, 204);
  console.log(`\n${passed} checks passed`);
}
