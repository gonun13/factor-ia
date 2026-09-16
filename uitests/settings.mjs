/**
 * The project settings panel and the agent node's model slot, in a real
 * browser, against the statically-served bundle an operator actually loads.
 *
 * Run it:  docker compose --profile uitest run --rm uitest node tests/settings.mjs
 *
 * It creates its own throwaway project and deletes it at the end, so it can run
 * against a live instance without disturbing anything already there — the
 * delete takes the project's `_factories/<project>/` directory with it, so a
 * run leaves no trace in the operator's workspace either. It asserts rather
 * than prints: a UI check nobody reads the output of is not a check.
 */

import { chromium } from "playwright";

const BASE = process.env.FACTOR_IA_URL ?? "http://127.0.0.1:8000";
const PROJECT = `uitest-settings-${Date.now().toString(36)}`;

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

// The Origin check in `security.py` covers every mutating request, so a bare
// fetch from node has to name one the server accepts.
const api = (path, init) =>
  fetch(`${BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", Origin: BASE, ...init?.headers },
  });

const created = await api("/api/projects", {
  method: "POST",
  body: JSON.stringify({ name: PROJECT, id: PROJECT }),
});
if (!created.ok) throw new Error(`could not create the test project: ${await created.text()}`);

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on("pageerror", (error) => {
    console.log(`FAIL  uncaught page error: ${error.message}`);
    process.exitCode = 1;
  });

  await page.goto(`${BASE}/#/p/${PROJECT}`, { waitUntil: "networkidle" });
  // A fresh project's factory is empty, so drop an agent from the palette —
  // the model slot is what this file is about. Scoped to the open menu because
  // the group's own trigger is called "Agents", which /^Agent/ also matches.
  await (await openPalette(page, "agents"))
    .getByRole("button", { name: /^Agent/ })
    .click();
  await page.waitForSelector(".slot.model");

  const slot = page.locator(".slot.model");
  const select = page.locator('select[aria-label="model override"]');
  const cog = page.getByRole("button", { name: "Project settings" });
  const saveSettings = page.getByRole("button", { name: /Save settings/ });
  // The inherit option is always the first, and it is what the node shows when
  // nothing overrides it.
  const inherited = async () => select.locator("option").first().innerText();

  check("the slot starts on the project default", await select.inputValue(), "");
  check("cog is not expanded", await cog.getAttribute("aria-expanded"), "false");
  // Dropping that node already dirtied the canvas, which is the point: what
  // must hold is that saving *settings* leaves this untouched, whatever it says.
  const pillBefore = await page.locator(".savebar .pill").innerText();

  // ---- the panel opens, and is empty until something is chosen -------------
  await cog.click();
  await page.waitForSelector(".slideover");
  check("the panel focuses its first field", await page.evaluate(() => document.activeElement?.id), "project-provider");
  check("save is disabled until something changes", await saveSettings.isDisabled(), true);
  const container = (await api("/api/agent/status").then((r) => r.json())).default_provider;
  check("the inherit option names the container's real provider", await page.locator("#project-provider option").allInnerTexts(), [
    `${container === "fake" ? "Fake (dry run)" : "Claude"} (default)`,
    "Claude",
    "Fake (dry run)",
  ]);
  const cliDefault = (await api("/api/agent/status").then((r) => r.json())).handshake.default_model;
  check("the inherit option names the CLI's real default model", (await page.locator("#project-model option").first().innerText()).trim(), `${cliDefault} (default)`);

  // ---- saving a default, and the node following it -------------------------
  // The model is chosen with the provider still inheriting: a provider that
  // reports its own id greys this select, and what is under test here is the
  // labelling equality between the panel and the node.
  const models = await page.locator("#project-model option").allInnerTexts();
  if (models.length < 2) throw new Error("the CLI offered no models — is the container signed in?");
  await page.locator("#project-model").selectOption({ index: 1 });
  // What the panel calls the model being saved. The node must end up naming the
  // same thing — that equality is the point of the whole labelling change.
  const chosenLabel = (await page.locator("#project-model option:checked").innerText()).trim();
  check("an unsaved change is flagged", await page.locator(".slideover .pill.dirty").count(), 1);
  await saveSettings.click();
  await page.waitForSelector(".slideover", { state: "detached" });

  check("the node's inherit option now names the project's model", (await inherited()).trim(), `${chosenLabel} (default)`);
  check("project settings leave the canvas's dirty pill exactly as it was", await page.locator(".savebar .pill").innerText(), pillBefore);

  // ---- a provider that reports its own model id ---------------------------
  // The list is per provider (`/api/agent/status` → `providers`), so choosing
  // the fake has to change what both pickers say. The bug this covers is the
  // quiet one: a project pinned to the fake went on offering five Claude ids
  // and naming a default that no dry run would ever report.
  await cog.click();
  await page.waitForSelector(".slideover");
  await page.locator("#project-provider").selectOption("fake");
  const fakeOffer = (await api("/api/agent/status").then((r) => r.json())).providers.fake;
  check("the model select greys out for a provider that ignores it", await page.locator("#project-model").isDisabled(), true);
  check("…and names what that provider will actually report", (await page.locator("#project-model option").first().innerText()).trim(), `${fakeOffer.default_model} (default)`);
  await saveSettings.click();
  await page.waitForSelector(".slideover", { state: "detached" });

  const chosen = (await api(`/api/projects/${PROJECT}`).then((r) => r.json())).settings;
  check("the provider persisted", chosen.default_provider, "fake");
  // The stored model id is still there and untouched — it is ignored, not lost.
  check("the saved model survives a provider that ignores it", chosen.default_model !== null, true);
  check("the node's model slot follows the provider, not the CLI", (await inherited()).trim(), `${fakeOffer.default_model} (default)`);
  check("…and the node's own override greys out with it", await select.isDisabled(), true);

  // ---- closing ------------------------------------------------------------
  await cog.click();
  await page.waitForSelector(".slideover");
  await page.keyboard.press("Escape");
  await page.waitForSelector(".slideover", { state: "detached" });
  check("Escape closes it and returns focus to the cog", await page.evaluate(() => document.activeElement?.getAttribute("aria-label")), "Project settings");

  await cog.click();
  await page.waitForSelector(".slideover");
  await page.mouse.click(200, 450);
  await page.waitForSelector(".slideover", { state: "detached" });
  check("a click on the scrim closes it", await page.locator(".slideover").count(), 0);

  // ---- the per-node override ----------------------------------------------
  const override = (await select.locator("option").all()).at(-1);
  const value = await override.getAttribute("value");
  await select.selectOption(value);
  check("an override wins over the default the option names", await select.inputValue(), value);
  check("a node edit belongs to the factory, so the canvas goes dirty", await page.locator(".savebar .pill").innerText(), "unsaved changes");
} finally {
  await browser.close();
  // Cleaning up is part of the test, so it is checked like the rest: a run that
  // silently leaves its project behind is a run that pollutes the next one.
  const removed = await api(`/api/projects/${PROJECT}`, { method: "DELETE" });
  check("the throwaway project deletes cleanly", removed.status, 204);
  check("…and is gone from the list", (await api(`/api/projects/${PROJECT}`)).status, 404);
  console.log(`\n${passed} checks passed`);
}
