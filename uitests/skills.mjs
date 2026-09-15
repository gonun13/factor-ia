/**
 * Skills, end to end in a real browser: imported on the main page, worn by an
 * agent node, saved into the factory file, and refused once the file is gone.
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
  await page.getByRole("button", { name: "Paste markdown instead" }).click();
  await page.locator('textarea[aria-label="Skill markdown"]').fill(SKILL_MD);
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await page.waitForSelector(`.skill-card .name:text-is("${SKILL}")`);
  check("the imported skill shows its description, not just its name",
    (await page.locator(".skill-card", { hasText: SKILL }).locator(".desc").innerText()).startsWith("Reads a repository"), true);

  // A second import of the same document is a conflict, and the only place
  // Replace is reachable from.
  await page.getByRole("button", { name: "Import a skill" }).click();
  await page.getByRole("button", { name: "Paste markdown instead" }).click();
  await page.locator('textarea[aria-label="Skill markdown"]').fill(SKILL_MD);
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await page.waitForSelector(".skill-import .error");
  check("a duplicate name offers Replace and nothing else", await page.locator(".skill-import .destructive").innerText(), "Replace it");
  await page.getByRole("button", { name: "Cancel" }).click();

  // ---- the plate, on the node ---------------------------------------------
  await page.goto(`${BASE}/#/p/${PROJECT}`, { waitUntil: "networkidle" });
  await page.waitForSelector(".node.type-agent .slot.skill");
  const plate = page.locator('.node.type-agent select[aria-label="skill"]');
  check("the plate starts empty, which is its normal state", await plate.inputValue(), "");
  await plate.selectOption(SKILL);
  check("…and names what is in force once chosen",
    (await page.locator(".node.type-agent .skill-what").innerText()).startsWith("Reads a repository"), true);

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
  const removed = await api(`/api/projects/${PROJECT}`, { method: "DELETE" });
  check("the throwaway project deletes cleanly", removed.status, 204);
  console.log(`\n${passed} checks passed`);
}
