/**
 * The run panel's state machine, in a real browser, against the bundle an
 * operator actually loads (`decisions/0024`).
 *
 * What it checks is the half the backend tests cannot see: that **two** buttons
 * are offered at every stage, which two, and — the point of the whole change —
 * that `Run` is never reachable while a run exists. Runs are expensive; a panel
 * that put the cheap answer and the expensive one under the same thumb is the
 * defect this file exists to keep fixed.
 *
 * The sequence: Run → Stop → Resume, then Cancel → confirm → Run.
 *
 * Run it:  docker compose --profile uitest run --rm uitest node tests/run-controls.mjs
 *
 * Same bargain as the others: its own throwaway project, pinned to the fake
 * provider so nothing here costs a token, deleted at the end, and it asserts
 * rather than prints.
 */

import { chromium } from "playwright";

const BASE = process.env.FACTOR_IA_URL ?? "http://127.0.0.1:8000";
const PROJECT = `uitest-runctl-${Date.now().toString(36)}`;

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

// **Free the slot first.** One run at a time, container-wide, so a run left
// behind by another test or by a person makes every assertion here a lie about
// this factory. Cancelling a halted run is itself part of what is under test.
for (const run of await (await api("/api/runs")).json()) {
  if (["running", "pending", "waiting_input"].includes(run.status)) {
    await api(`/api/runs/${run.id}/cancel`, { method: "POST" });
  }
}

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
if (!configured.ok) throw new Error(`could not pin to the fake provider: ${await configured.text()}`);

// A line slow enough to catch mid-run. The fake adapter sleeps
// `FACTOR_IA_FAKE_DELAY` (0.35s by default) **between every event**, and the
// success script emits a dozen, so an ordinary run lasts several seconds —
// which is what makes Stop observable at all. Against an instant agent the run
// would be over before a click landed and this file would assert on a finished
// run every time.
const graph = {
  nodes: [
    { id: "n-in", definition: { type: "input", label: "Request", config: { kind: "request" } } },
    {
      id: "n-agent",
      definition: {
        type: "agent",
        label: "Agent",
        codebase_id: codebase,
        position: { x: 260, y: 0 },
        config: { fake_script: "success" },
      },
    },
    { id: "n-out", definition: { type: "output", label: "Commit", position: { x: 520, y: 0 }, config: { kind: "commit" } } },
  ],
  edges: [
    { id: "e1", source: "n-in", target: "n-agent" },
    { id: "e2", source: "n-agent", target: "n-out", source_handle: "success" },
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

  const button = (name) => page.locator(".runbox-head button", { hasText: name });
  const status = () => page.locator(".runbox").getAttribute("data-run-status");
  /** Poll until the run reaches one of `wanted`, or say what it reached instead.
   *
   * A poll rather than the rAF-driven wait, which proved flaky immediately
   * after a click here — and, the reason worth keeping, whose timeout says only
   * "15000ms exceeded" where this says which state the run is actually in.
   * Failures are sentences, in a test as much as anywhere. */
  const settle = async (wanted, ms = 20000) => {
    const deadline = Date.now() + ms;
    for (;;) {
      const now = await status();
      if (wanted.includes(now)) return now;
      if (Date.now() > deadline) {
        throw new Error(`run never reached ${wanted.join("|")} — it is ${now}`);
      }
      await page.waitForTimeout(200);
    }
  };
  const buttons = async () =>
    (await page.locator(".runbox-head button").allInnerTexts()).map((text) => text.trim());

  await page.goto(`${BASE}/#/p/${PROJECT}`, { waitUntil: "networkidle" });
  await page.waitForSelector(".runbox");

  // ---- idle: one button, and it is Run ------------------------------------
  check("an idle canvas offers Run alone", await buttons(), ["▶ Run"]);

  // ---- running: Stop and Cancel -------------------------------------------
  await button("Run").click();
  await page.waitForSelector(".request textarea", { timeout: 10000 });
  await page.locator(".request textarea").fill("tidy the readme");
  await page.locator(".request button[type=submit]").click();

  await settle(["running"]);
  check("a running run offers Stop and Cancel", await buttons(), ["■ Stop", "✕ Cancel"]);
  check("…and Run is not among them", await button("▶ Run").count(), 0);

  // ---- stopped: Resume and Cancel -----------------------------------------
  await button("Stop").click();
  await settle(["stopped"]);
  check("a stopped run offers Resume and Cancel", await buttons(), ["▶ Resume", "✕ Cancel"]);
  check("…and Run is still not reachable", await button("▶ Run").count(), 0);

  // ---- the confirmation ----------------------------------------------------
  await button("Cancel").click();
  check(
    "Cancel asks first, in the panel rather than over it",
    await page.locator(".run-confirm-question").innerText(),
    "Cancel this run?",
  );
  check("…offering both answers", await buttons(), ["Cancel run", "Keep it"]);

  // **No changes nothing.** The requirement in as many words: the run is still
  // stopped, and the controls come back exactly as they were.
  await button("Keep it").click();
  check("Keep it restores the controls untouched", await buttons(), ["▶ Resume", "✕ Cancel"]);
  check(
    "…and the run is still stopped",
    await page.locator(".runbox").getAttribute("data-run-status"),
    "stopped",
  );

  // ---- resume, then cancel for real ---------------------------------------
  await button("Resume").click();
  await settle(["running", "succeeded"]);
  check("Resume puts the run back on the line", true, true);

  // Drive it to a halted state and cancel it for real. **Tolerant of the
  // race on purpose**: the resumed run may finish before a Stop click lands,
  // and a test that assumed otherwise would fail on the fake agent's timing
  // rather than on anything this file is about.
  if ((await status()) === "running") {
    await button("Stop").click();
    await settle(["stopped", "succeeded", "failed"]);
  }

  // `failed` as well as `stopped`: a resumed run whose commit node finds
  // nothing new to commit fails there, deliberately, and a failed run is
  // cancellable for exactly the same reason a stopped one is.
  if (["stopped", "failed"].includes(await status())) {
    check("a halted run offers Resume and Cancel", await buttons(), ["▶ Resume", "✕ Cancel"]);
    await button("Cancel").click();
    await button("Cancel run").click();
    await settle(["cancelled"]);
    check("a confirmed Cancel returns the panel to Run", await buttons(), ["▶ Run"]);
  } else {
    // The other end of the same rule: a run that finished offers Run and
    // nothing else — there is nothing left to stop, resume or withdraw.
    check("a succeeded run offers Run alone", await buttons(), ["▶ Run"]);
  }

  console.log(`\n${passed} checks passed`);
} finally {
  await browser.close();
  await api(`/api/projects/${PROJECT}`, { method: "DELETE" });
}
