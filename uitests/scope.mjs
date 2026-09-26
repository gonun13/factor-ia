/**
 * The scope node — a human node that negotiates — in a real browser.
 *
 * `gate.mjs` covers the other kind of human node: one question, two buttons.
 * What this checks is the half of the negotiating node the backend tests cannot
 * see: that it is its own machine in the palette, that it carries a skill plate
 * and says so when it is empty, and — the part that matters — that the run panel
 * puts the agent's questions to the operator **one after another**, that a
 * number key picks an option, that "other" takes something the agent did not
 * offer, and that the document made out of those answers reaches the next node.
 *
 * The transport claim is what all of that adds up to: `DESIGN.md` open question
 * 2 reserved the right to move to `/ws` for exactly this node, and this test is
 * a multi-turn conversation conducted entirely over SSE down and POST up.
 *
 * Run it:  docker compose --profile uitest run --rm uitest node tests/scope.mjs
 *
 * Same bargain as the others: its own throwaway project and its own throwaway
 * skill, both deleted at the end, and it asserts rather than prints.
 */

import { chromium } from "playwright";

const BASE = process.env.FACTOR_IA_URL ?? "http://127.0.0.1:8000";
const STAMP = Date.now().toString(36);
const PROJECT = `uitest-scope-${STAMP}`;
const SKILL = `uitest-scoping-${STAMP}`;

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

// The skill the scope node wears. Required, not optional — a scope node with
// none is refused by the run check, which this test also asserts.
const skill = await api("/api/skills", {
  method: "POST",
  body: JSON.stringify({
    text: `---\nname: ${SKILL}\ndescription: Interviews an operator about a change before anyone builds it.\n---\n\n# Scoping\n\nAsk what the change is for, and how we will know it worked.\n`,
    filename: `${SKILL}.md`,
    overwrite: true,
  }),
});
if (!skill.ok) throw new Error(`could not import the test skill: ${await skill.text()}`);

const created = await api("/api/projects", {
  method: "POST",
  body: JSON.stringify({ name: PROJECT, id: PROJECT, codebase_ids: [codebase] }),
});
if (!created.ok) throw new Error(`could not create the test project: ${await created.text()}`);

// Fake at the project level: the scope session's two questions and its document
// come from the fake's `scope` script, which is the one script that *waits* for
// an answer — so everything below is a real parked run, at no cost.
const configured = await api(`/api/projects/${PROJECT}`, {
  method: "PATCH",
  body: JSON.stringify({ settings: { default_provider: "fake", default_model: null } }),
});
if (!configured.ok) throw new Error(`could not pin the project to the fake provider: ${await configured.text()}`);

// The shape the node is for: an interview at the **head** of the line, before
// anything has been built, handing a brief to the machine that does the work.
const graph = {
  nodes: [
    { id: "n-in", definition: { type: "input", label: "Request", config: { kind: "request" } } },
    { id: "n-scope", definition: { type: "human", label: "Scope", position: { x: 260, y: 0 }, config: { kind: "scope", skill: SKILL } } },
    { id: "n-agent", definition: { type: "agent", label: "Agent", codebase_id: codebase, position: { x: 560, y: 0 }, config: {} } },
    { id: "n-out", definition: { type: "output", label: "Commit", position: { x: 860, y: 0 }, config: { kind: "commit" } } },
  ],
  edges: [
    { id: "e1", source: "n-in", target: "n-scope" },
    { id: "e2", source: "n-scope", target: "n-agent", source_handle: "success" },
    { id: "e3", source: "n-agent", target: "n-out", source_handle: "success" },
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

  // ---- the machine --------------------------------------------------------
  const humanMenu = await openPalette(page, "human");
  check("the palette offers a scope node beside the sign-off", await humanMenu.getByRole("button", { name: /^Scope/ }).count(), 1);
  check("…and the sign-off is still there", await humanMenu.getByRole("button", { name: /^Sign-off/ }).count(), 1);
  check("the scope node says what kind it is", (await page.locator(".node.type-human .eyebrow").innerText()).toLowerCase(), "scope");
  check("…and carries a skill plate rather than a question", await page.locator('.node.type-human select[aria-label="skill"]').inputValue(), SKILL);
  check("…and no question field, which is not the operator's to write", await page.locator('.node.type-human input[aria-label="what to ask the operator"]').count(), 0);
  check("it draws both ports, like every human node", await page.locator(".node.type-human").locator(".react-flow__handle-right").evaluateAll((els) => els.map((el) => el.getAttribute("data-handleid")).sort()), ["fail", "success"]);

  // ---- a scope node with no skill is refused before the run ---------------
  await page.locator('.node.type-human select[aria-label="skill"]').selectOption("");
  await page.waitForSelector(".node.type-human .warn");
  check("clearing the skill says why it matters, on the node", await page.locator(".node.type-human .warn").innerText(), "Required — attach one.");
  const blocker = page.locator(".problems li", { hasText: "no skill attached" });
  check("…and blocks the run, in the sentence the server refuses it with", await blocker.count(), 1);
  await page.locator('.node.type-human select[aria-label="skill"]').selectOption(SKILL);
  await page.waitForSelector(".node.type-human .warn", { state: "detached" });

  // ---- the interview ------------------------------------------------------
  await page.locator(".runbox button.run").click();
  await page.waitForSelector(".request textarea");
  await page.locator(".request textarea").fill("Make the importer resumable.");
  await page.getByRole("button", { name: /Submit the request/ }).click();

  await page.waitForSelector("form.question", { timeout: 60_000 });
  check("the node is the one waiting, not the run panel alone", (await page.locator(".node.type-human .status-word").innerText()).toLowerCase(), "waiting input");
  check("the form says who is asking", await page.locator("form.question label").innerText(), "Scope is asking");
  check("…and renders the agent's question", (await page.locator("form.question legend").innerText()).includes("What is this change for?"), true);
  check("…under the agent's own label for it", (await page.locator(".question-chip").innerText()).trim().toLowerCase(), "goal");
  check("…with its options numbered for the keyboard", await page.locator(".question-option kbd").allInnerTexts(), ["1", "2"]);
  check("…and Answer is refused until there is one", await page.getByRole("button", { name: "Answer" }).isDisabled(), true);

  // A number key picks an option — the whole reason an interview is bearable.
  await page.locator("form.question").press("1");
  check("pressing 1 chooses the first option", await page.locator(".question-option.chosen .question-label").innerText(), "Fixing a bug");
  check("…and Answer is live", await page.getByRole("button", { name: "Answer" }).isDisabled(), false);
  await page.getByRole("button", { name: "Answer" }).click();

  // ---- the second question, which is the claim ----------------------------
  // One session, parked twice. A gate cannot do this and neither could this
  // node before: the run went back to running and stopped again, on SSE.
  await page.waitForFunction(
    () => document.querySelector("form.question legend")?.textContent?.includes("How will we know"),
    undefined,
    { timeout: 60_000 },
  );
  check("the session asks again rather than ending", (await page.locator("form.question legend").innerText()).includes("How will we know it worked?"), true);

  // "Other" takes something the agent never offered, which is why it is always
  // there: an agent's options are a guess at what you are going to say.
  await page.locator('form.question input[aria-label^="another answer"]').fill("the importer survives a kill -9");
  await page.getByRole("button", { name: "Answer" }).click();

  // ---- the document -------------------------------------------------------
  await page.waitForSelector('.runbox[data-run-status="succeeded"]', { timeout: 60_000 });
  check("the form is gone once the interview is over", await page.locator("form.question").count(), 0);
  check("the scope node went green", await page.locator(".node.type-human").getAttribute("data-status"), "succeeded");
  check("the machine after it ran on what the interview produced", await page.locator(".node.type-agent").getAttribute("data-status"), "succeeded");
  check("…and the line reached its commit", await page.locator(".node.type-output").getAttribute("data-status"), "succeeded");

  // The answers, in the document, on the belt — read back off the run record
  // rather than off the canvas, because what the next machine was handed is the
  // claim and the canvas is only how it is shown.
  const runs = await (await api(`/api/runs`)).json();
  const run = await (await api(`/api/runs/${runs[0].id}`)).json();
  const document = run.node_runs.find((one) => one.node_id === "n-scope").output_text;
  check("the document holds the option that was clicked", document.includes("Fixing a bug"), true);
  check("…and the answer that was typed", document.includes("the importer survives a kill -9"), true);
  check("…and it is a document, not a transcript", document.trimStart().startsWith("#"), true);
  const scopeRun = run.node_runs.find((one) => one.node_id === "n-scope");
  check("…and the node says how many questions it took", scopeRun.detail, "scoped over 2 questions");
} finally {
  await browser.close();
  const removed = await api(`/api/projects/${PROJECT}`, { method: "DELETE" });
  check("the throwaway project deletes cleanly", removed.status, 204);
  const dropped = await api(`/api/skills/${SKILL}`, { method: "DELETE" });
  check("the throwaway skill deletes cleanly", dropped.status, 204);
  console.log(`\n${passed} checks passed`);
}
