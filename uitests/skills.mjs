/**
 * Skills, end to end in a real browser: imported on the main page, worn by an
 * agent node, saved into the factory file, and refused once the file is gone.
 *
 * A document written for an assistant goes in too, because that is the case
 * the library exists to survive: it imports, the lines that will not hold are
 * named on the way in, and the contract that makes them survivable can be read
 * from the page.
 *
 * What this checks that the backend tests cannot: that the library renders
 * where it was put (below the projects, because a skill belongs to no project),
 * that the agent node's third plate writes `config.skill` into the graph the
 * canvas saves, and that deleting a skill out from under a node turns into the
 * same sentence on the canvas and in the run refusal.
 *
 * Run it:  docker compose --profile uitest run --rm uitest node tests/skills.mjs
 *
 * Same bargain as `gate.mjs`: its own throwaway project and skill, both removed
 * at the end, and it asserts rather than prints.
 */

import { chromium } from "playwright";

const BASE = process.env.FACTOR_IA_URL ?? "http://127.0.0.1:8000";
const STAMP = Date.now().toString(36);
const PROJECT = `uitest-skills-${STAMP}`;
const SKILL = `uitest-recon-${STAMP}`;

const SKILL_MD = `---
name: ${SKILL}
description: Reads a repository and reports what it is, before touching anything.
---

# Recon

Read the repository before changing a line of it.
`;

// The other half of the library's job: a document written for an assistant,
// imported unchanged. It is not refused — the machine contract is what makes
// it survivable — and the notes name the lines a line will read differently.
const CHATTY = `uitest-chatty-${STAMP}`;
const CHATTY_MD = `---
name: ${CHATTY}
description: Starts a project. Use when the user lands somewhere new.
---

# Kickoff

Read the folder first.
Ask the user which stack they want.
`;

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

// One machine on a line, saved through the API: what is under test is the
// plate and what it writes, not the drawing of a graph.
const graph = {
  nodes: [
    { id: "n-in", definition: { type: "input", label: "Request", config: { kind: "request" } } },
    { id: "n-agent", definition: { type: "agent", label: "Agent", codebase_id: codebase, position: { x: 260, y: 0 }, config: { adapter: "fake" } } },
    { id: "n-out", definition: { type: "output", label: "Commit", position: { x: 520, y: 0 }, config: { kind: "commit" } } },
  ],
  edges: [
    { id: "e1", source: "n-in", target: "n-agent" },
    { id: "e2", source: "n-agent", target: "n-out" },
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

  // ---- the library, on the main page --------------------------------------
  await page.goto(`${BASE}/#/`, { waitUntil: "networkidle" });
  await page.waitForSelector(".skills-section");
  check("the library sits below the projects", await page.evaluate(() => {
    const grid = document.querySelector(".project-grid");
    const skills = document.querySelector(".skills-section");
    return grid && skills ? grid.compareDocumentPosition(skills) === Node.DOCUMENT_POSITION_FOLLOWING : null;
  }), true);

  await page.getByRole("button", { name: "Import a skill" }).click();
  await page.getByRole("button", { name: "Paste Markdown" }).click();
  await page.locator('textarea[aria-label="Skill markdown"]').fill(SKILL_MD);
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await page.waitForSelector(`.skill-card .name:text-is("${SKILL}")`);
  check("the imported skill's card is its name alone, no description",
    await page.locator(".skill-card", { hasText: SKILL }).locator(".desc").count(), 0);

  check("an import says so, and says nothing was read as written for a chat",
    (await page.locator(".skill-imported-head").innerText()).includes("nothing in it reads as written for a chat"), true);
  check("…with no notes under it", await page.locator(".skill-imported .skill-notes").count(), 0);
  await page.locator(".skill-imported").getByRole("button", { name: "Dismiss" }).click();

  // ---- a skill written for an assistant -----------------------------------
  // Imported, never refused, and the lines that will not hold are named on the
  // way in — beside the file the operator just chose.
  await page.getByRole("button", { name: "Import a skill" }).click();
  await page.getByRole("button", { name: "Paste Markdown" }).click();
  await page.locator('textarea[aria-label="Skill markdown"]').fill(CHATTY_MD);
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await page.waitForSelector(`.skill-card .name:text-is("${CHATTY}")`);
  check("it imports all the same", await page.locator(".skill-imported .error").count(), 0);
  check("and one line is named", await page.locator(".skill-imported .skill-notes li").count(), 1);
  check("…by its number in the document",
    await page.locator(".skill-imported .skill-note-line").innerText(), "line 9");
  check("…with what holds instead",
    (await page.locator(".skill-imported .skill-note-what").innerText()).includes("nobody to ask"), true);

  // The same notes on reopening: they belong to the document, not to the import.
  await page.locator(".skill-imported").getByRole("button", { name: "Open it" }).click();
  await page.waitForSelector(".skill-editor .skill-notes");
  check("the editor shows them under the document",
    await page.locator(".skill-editor .skill-notes li").count(), 1);
  await page.locator(".skill-editor .node-log-close").click();

  // What makes such a line survivable, in the words the agent actually gets.
  const contractButton = page.getByRole("button", { name: "Machine Contract" });
  await contractButton.click();
  await page.waitForSelector(".machine-contract-text");
  check("the library can show what every worn skill is told",
    (await page.locator(".machine-contract-text").innerText()).includes("There is nobody to ask"), true);
  check("the button says it is open", await contractButton.getAttribute("aria-expanded"), "true");
  await contractButton.click();
  check("a second press puts it away", await page.locator(".machine-contract-text").count(), 0);

  // A second import of the same document is a conflict, and the only place
  // Replace is reachable from.
  await page.getByRole("button", { name: "Import a skill" }).click();
  await page.getByRole("button", { name: "Paste Markdown" }).click();
  await page.locator('textarea[aria-label="Skill markdown"]').fill(SKILL_MD);
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await page.waitForSelector(".skill-import .error");
  check("a duplicate name offers Replace and nothing else", await page.locator(".skill-import .destructive").innerText(), "Replace it");
  await page.getByRole("button", { name: "Cancel" }).click();

  // ---- system skills: in the library, never in a picker -------------------
  // A knowledge node applies one to what it is handed; nothing wears one. So
  // the library must show all three and offer no delete, and the node's plate
  // must not list them at all.
  const SYSTEM = "knowledge-codebase";
  check("a system skill is in the library",
    await page.locator(".skill-card", { hasText: SYSTEM }).count(), 1);
  check("it says so on the card",
    (await page.locator(".skill-card", { hasText: SYSTEM }).locator(".skill-badge").innerText()).toLowerCase(),
    "system");
  check("and carries the knowledge tone, not the ordinary one",
    await page.locator(".skill-card", { hasText: SYSTEM }).evaluate(
      (el) => el.classList.contains("is-system")), true);
  check("the imported skill does not",
    await page.locator(".skill-card", { hasText: SKILL }).first().evaluate(
      (el) => el.classList.contains("is-system")), false);

  // System first, then everything by name — so the shipped four are a block
  // at the top and an import has a stable place to start.
  // The knowledge library reuses `.skill-grid` and `.skill-card`, so its cards
  // are excluded by their own class. The badge sits on its own line, outside
  // `.name`, so the name is the element's only text node.
  const order = await page
    .locator(".skill-grid .skill-card:not(.knowledge-card) .name")
    .evaluateAll((els) => els.map((el) => el.firstChild.textContent.trim()));
  const shipped = ["commit-message", "knowledge-codebase", "knowledge-general", "knowledge-project"];
  check("the system skills sort to the top",
    shipped.every((name) => order.slice(0, shipped.length).includes(name)), true);
  check("and the imported one is below them",
    order.indexOf(SKILL) >= shipped.length, true);
  const systemSlot = page.locator(".skill-slot").filter({ hasText: SYSTEM });
  check("and it offers no delete, because a node type is built on it",
    await systemSlot.locator(".card-delete").count(), 0);

  // Reverting is offered only once there is an edit to undo.
  const edited = await api("/api/skills", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      text: `---\nname: ${SYSTEM}\ndescription: Ours for this test.\n---\n\nOurs.\n`,
      overwrite: true,
    }),
  });
  check("a system skill is edited by the ordinary import", edited.status, 201);
  await page.reload({ waitUntil: "networkidle" });
  check("an overridden one offers Revert",
    await systemSlot.locator(".card-delete").count(), 1);
  const reverted = await api(`/api/skills/${SYSTEM}`, { method: "DELETE" });
  check("reverting succeeds", reverted.status, 204);
  const refused = await api(`/api/skills/${SYSTEM}`, { method: "DELETE" });
  check("but with nothing to revert it cannot be deleted", refused.status, 405);
  await page.reload({ waitUntil: "networkidle" });
  check("and the skill is still there afterwards",
    await page.locator(".skill-card", { hasText: SYSTEM }).count(), 1);

  // Opening one warns before the document, in the card's tone — a warning
  // under the textarea is one nobody reads before typing.
  await page.locator(".skill-card", { hasText: SYSTEM }).click();
  await page.waitForSelector(".skill-editor");
  check("the editor carries the card's tone",
    await page.locator(".skill-editor").evaluate((el) => el.classList.contains("is-system")), true);
  check("it warns what editing this means",
    (await page.locator(".skill-editor-warning strong").innerText()),
    "This skill is built into the knowledge node.");
  check("the warning names three consequences",
    await page.locator(".skill-editor-warning li").count(), 3);
  check("and sits above the document",
    await page.locator(".skill-editor").evaluate((el) => {
      const warn = el.querySelector(".skill-editor-warning");
      return !!(warn.compareDocumentPosition(el.querySelector("textarea")) &
        Node.DOCUMENT_POSITION_FOLLOWING);
    }), true);
  await page.locator(".skill-editor .node-log-close").click();

  await page.locator(".skill-card", { hasText: SKILL }).first().click();
  await page.waitForSelector(".skill-editor");
  check("an ordinary skill gets no warning",
    await page.locator(".skill-editor-warning").count(), 0);
  await page.locator(".skill-editor .node-log-close").click();

  // ---- the plate, on the node ---------------------------------------------
  await page.goto(`${BASE}/#/p/${PROJECT}`, { waitUntil: "networkidle" });
  await page.waitForSelector(".node.type-agent .slot.skill");
  check("no system skill is offered on a node",
    await page.locator('.node.type-agent select[aria-label="skill"] option')
      .filter({ hasText: "knowledge-" }).count(), 0);
  const plate = page.locator('.node.type-agent select[aria-label="skill"]');
  check("the plate starts empty, which is its normal state", await plate.inputValue(), "");
  await plate.selectOption(SKILL);
  // The description is a popup under the pointer now, portalled to the body,
  // so the check has to look the way a person does: rest on the plate and
  // wait. A plate still holding focus is not hovering — let go of it first.
  await plate.blur();
  await page.mouse.move(0, 0);
  const tipSel = "body > .node-note-popup.skill-what";
  check("the description stays off the resting node",
    await page.locator(tipSel).count(), 0);

  const plateBox = await page.locator(".node.type-agent .slot.skill").boundingBox();
  const px = plateBox.x + plateBox.width / 2;
  const py = plateBox.y + plateBox.height / 2;
  await page.mouse.move(px - 30, py - 30);
  await page.mouse.move(px, py);
  await page.waitForTimeout(600);
  check("…and does not open the moment the pointer crosses the plate",
    await page.locator(tipSel).count(), 0);
  await page.waitForTimeout(700);
  check("…and names what is in force once chosen, a second into the hover",
    (await page.locator(tipSel).innerText()).startsWith("Reads a repository"), true);
  // Under the pointer, not glued to the plate: every description on a node
  // opens just below where you are pointing.
  const tip = await page.locator(tipSel).boundingBox();
  check("the popup hangs below the pointer", tip.y > py && tip.y < py + 40, true);
  await page.mouse.move(0, 0);
  await page.waitForTimeout(200);
  check("and closes when the pointer leaves", await page.locator(tipSel).count(), 0);

  await page.keyboard.press("Meta+s");
  await page.waitForTimeout(800);
  const file = await (await api(`/api/projects/${PROJECT}/factories/main`)).json();
  const agent = file.nodes.find((node) => node.id === "n-agent");
  check("the skill is saved into the factory file by name", agent.definition.config.skill, SKILL);

  // ---- and what happens when it goes away ---------------------------------
  const removed = await api(`/api/skills/${SKILL}`, { method: "DELETE" });
  check("the skill deletes", removed.status, 204);

  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".node.type-agent .slot.skill .warn");
  check("the node says the skill is gone", await page.locator(".node.type-agent .slot.skill .warn").innerText(), "No longer in _skills/.");

  // The same sentence the server refuses with — the canvas and the run check
  // are deliberately not allowed to disagree about this.
  await page.locator(".runbox button.run").click();
  await page.waitForSelector(".problems li");
  check("…and the run is refused rather than started",
    (await page.locator(".problems").innerText()).includes("no longer in /workspace/_skills"), true);
} finally {
  await browser.close();
  await api(`/api/skills/${SKILL}`, { method: "DELETE" });
  await api(`/api/skills/${CHATTY}`, { method: "DELETE" });
  const removed = await api(`/api/projects/${PROJECT}`, { method: "DELETE" });
  check("the throwaway project deletes cleanly", removed.status, 204);
  console.log(`\n${passed} checks passed`);
}
