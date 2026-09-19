/**
 * A person closing the loop: rejecting sends the work back with a message.
 *
 * `loop.mjs` owns the reviewer's loop and `gate.mjs` owns the sign-off node.
 * This is the seam between them — the graph shape `decisions/0009` refused
 * until a human judge was allowed to close a cycle — and it checks the three
 * things neither of the others can see:
 *
 * - the canvas works out on its own that a *sign-off's* fail belt comes back
 *   round, draws it as a return conveyor, and heals the file onto the `loop`
 *   entry. `back_edges` answers the same question server-side, and this is
 *   what catches the two answers drifting apart.
 * - **Reject is disabled until the note has text.** On a back-edge the note
 *   replaces what the gate passes on, so an empty one sends a worker round
 *   again with nothing to act on. The server refuses it too; this is the half
 *   an operator actually meets.
 * - the loop turns on a human's say-so: the worker reaches lap 2, and
 *   approving on the second look commits exactly once.
 *
 * Run it:  docker compose --profile uitest run --rm uitest node tests/gate-loop.mjs
 *
 * Same bargain as the rest: its own throwaway project, deleted at the end, and
 * it asserts rather than prints.
 */

import { chromium } from "playwright";

const BASE = process.env.FACTOR_IA_URL ?? "http://127.0.0.1:8000";
const PROJECT = `uitest-gateloop-${Date.now().toString(36)}`;

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

const configured = await api(`/api/projects/${PROJECT}`, {
  method: "PATCH",
  body: JSON.stringify({ settings: { default_provider: "fake", default_model: null } }),
});
if (!configured.ok) throw new Error(`could not pin the project to the fake provider: ${await configured.text()}`);

// The reviewer's canonical loop with a person in the reviewer's place. Saved
// with **no `target_handle` on any edge**, which is both the shape of every
// file written before the entries existed and the only way to check that the
// canvas decides for itself which belt comes back round.
const graph = {
  nodes: [
    { id: "n-in", definition: { type: "input", label: "Request", config: { kind: "request" } } },
    { id: "n-work", definition: { type: "agent", label: "Build it", codebase_id: codebase, position: { x: 300, y: 0 }, config: { kind: "worker" } } },
    { id: "n-gate", definition: { type: "human", label: "Sign-off", position: { x: 640, y: 0 }, config: { kind: "approval", prompt: "Does this look right?" } } },
    { id: "n-out", definition: { type: "output", label: "Commit", position: { x: 980, y: 0 }, config: { kind: "commit" } } },
  ],
  edges: [
    { id: "e1", source: "n-in", target: "n-work" },
    { id: "e2", source: "n-work", target: "n-gate" },
    { id: "e3", source: "n-gate", target: "n-out", source_handle: "success" },
    { id: "e4", source: "n-gate", target: "n-work", source_handle: "fail" },
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

  // ---- the return conveyor ------------------------------------------------
  //
  // Drawn by hand as a path that drops below both endpoints, rather than as a
  // smear back across two machines. A sign-off's reject belt earning the same
  // drawing as a reviewer's is the visible half of the rule change.
  check("the sign-off's reject belt is drawn as a return belt", await page.locator(".belt-back").count(), 1);
  check("…and still as a fail belt", await page.locator(".belt-fail.belt-back").count(), 1);
  check("every belt is on screen", await page.locator(".react-flow__edge").count(), graph.edges.length);

  // ---- the file heals onto the loop entry ---------------------------------
  //
  // Asserted through a save, because the claim is about two things: what the
  // canvas drew, and that a factory opened and saved comes back naming its
  // entries without the operator redrawing anything. Normalising on open does
  // not dirty the graph, so there has to be a real edit for Save to be live.
  await page.locator('.node.kind-worker input.node-title').fill("Build it twice");
  await page.locator(".savebar button.primary").click();
  await page.waitForSelector(".pill.saved");
  const healed = await (await api(`/api/projects/${PROJECT}/factories/main`)).json();
  const entryOf = (id) => healed.edges.find((edge) => edge.id === id)?.target_handle ?? null;
  check("the sign-off's reject belt is saved onto the loop entry", entryOf("e4"), "loop");
  check("…and the belt down the line onto the forward one", entryOf("e2"), "forward");

  // ---- the note is not optional on a belt that loops ----------------------
  await page.locator(".runbox button.run").click();
  await page.waitForSelector(".request textarea");
  await page.locator(".request textarea").fill("Add the bounds check.");
  await page.getByRole("button", { name: /Submit the request/ }).click();

  await page.waitForSelector("form.decision", { timeout: 60_000 });
  check(
    "rejecting is refused while there is nothing to send back",
    await page.getByRole("button", { name: "Reject" }).isDisabled(),
    true,
  );
  check("…and the form says why", await page.locator(".decision-hint").count(), 1);
  check(
    "approving is never gated on a note",
    await page.getByRole("button", { name: "Approve" }).isDisabled(),
    false,
  );

  await page.locator("form.decision textarea").fill("the bounds check is still missing");
  check(
    "typing the reason enables the button",
    await page.getByRole("button", { name: "Reject" }).isDisabled(),
    false,
  );
  check("…and takes the explanation away with it", await page.locator(".decision-hint").count(), 0);

  // ---- the loop turns on a person's say-so --------------------------------
  await page.getByRole("button", { name: "Reject" }).click();

  await page.waitForSelector(".node.kind-worker .lap", { timeout: 60_000 });
  check(
    "the worker is sent round again and says which lap it is on",
    (await page.locator(".node.kind-worker .lap").innerText()).toLowerCase(),
    "lap 2",
  );

  // The gate is asked again on the second lap — being asked again is what
  // sending the work back *means*, and the form has to come back for it.
  await page.waitForSelector("form.decision", { timeout: 60_000 });
  await page.getByRole("button", { name: "Approve" }).click();

  await page.waitForSelector('.runbox[data-run-status="succeeded"]', { timeout: 120_000 });
  check("the second look approved", await page.locator(".node.type-human").getAttribute("data-status"), "succeeded");
  check("…and the line reached its commit", await page.locator(".node.type-output").getAttribute("data-status"), "succeeded");
  check("the finished worker is green, not still showing a lap in flight", await page.locator(".node.kind-worker").getAttribute("data-status"), "succeeded");
  check("the form is gone once it is answered", await page.locator("form.decision").count(), 0);
} finally {
  await browser.close();
  const removed = await api(`/api/projects/${PROJECT}`, { method: "DELETE" });
  check("the throwaway project deletes cleanly", removed.status, 204);
  console.log(`\n${passed} checks passed`);
}
