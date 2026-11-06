// End-to-end tests of the local UI v2: the real app.js runs in jsdom and talks to a REAL API server
// (started by tests/test_ui_e2e.py). This is not a real browser: layout/CSS are not exercised.
import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

const base = process.env.UI_BASE;
const token = process.env.UI_TOKEN;

async function open(hash = "") {
  return JSDOM.fromURL(base + "/" + hash, {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    beforeParse(window) {
      window.fetch = (url, opts) => fetch(new URL(url, base), opts);
      window.confirm = () => true;
    },
  });
}
async function until(fn, what, ms = 8000) {
  const start = Date.now();
  let last;
  while (Date.now() - start < ms) {
    try { const v = fn(); if (v) return v; } catch (err) { last = err; }
    await new Promise((r) => setTimeout(r, 20));
  }
  throw new Error("timed out waiting for: " + what + (last ? " (" + last.message + ")" : ""));
}
const $ = (dom, id) => dom.window.document.getElementById(id);
const text = (dom, id) => ($(dom, id) ? $(dom, id).textContent : "");
const click = (dom, id) => $(dom, id).dispatchEvent(new dom.window.Event("click", { bubbles: true }));
const type = (dom, id, value) => { $(dom, id).value = value; $(dom, id).dispatchEvent(new dom.window.Event("input", { bubbles: true })); };
const go = (dom, path) => { dom.window.location.hash = "#" + path; };
const key = (dom, k, opts = {}, target) => (target || dom.window.document.body).dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true, ...opts }));
const booted = async (dom) => { await until(() => !$(dom, "shell").hidden, "app shell"); return dom; };
const bodyText = (dom) => dom.window.document.getElementById("main").textContent;
const toasts = (dom) => text(dom, "toasts");

const CASE = {
  case_id: "ui-greet", name: "Greeting works",
  input: { action: "greet", name: "Ava" },
  assertions: [{ path: "message", operator: "equals", expected: "Hello, Ava!" }],
};

test("boot: the token leaves the address bar, the dashboard shows the empty state and onboarding", async () => {
  const dom = await open("#token=" + token);
  await booted(dom);
  assert.ok(!dom.window.location.href.includes(token));
  assert.equal(dom.window.location.hash, "#/overview");
  await until(() => $(dom, "onboarding"), "onboarding card");
  assert.equal(text(dom, "stat-cases"), "0");
  assert.equal(dom.window.document.querySelector('#nav a[aria-current="page"]').dataset.nav, "overview");
  assert.ok($(dom, "login").hidden);
});

test("login: wrong token is rejected with a message, the right one connects", async () => {
  const dom = await open();
  await until(() => !$(dom, "login").hidden, "login form");
  assert.ok($(dom, "shell").hidden);
  type(dom, "token-input", "definitely-wrong");
  $(dom, "login-card").dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
  await until(() => toasts(dom).includes("token was rejected"), "rejection toast");
  assert.ok($(dom, "shell").hidden && !$(dom, "login").hidden);
  type(dom, "token-input", token);
  $(dom, "login-card").dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
  await booted(dom);
  assert.equal($(dom, "token-input").value, "");
});

test("demo flow: load demo case, run twice, compare, report, delete", async () => {
  const dom = await open("#token=" + token);
  await booted(dom);
  await until(() => $(dom, "demo-load"), "demo button");
  click(dom, "demo-load");
  await until(() => dom.window.location.hash === "#/cases/demo-greet", "navigated to the demo case");
  await until(() => $(dom, "run-go"), "case page");
  await until(() => text(dom, "assertions-body").includes("Hello, Ava!"), "assertions rendered readably");
  $(dom, "run-runner").value = "local-legacy";
  click(dom, "run-go");
  await until(() => $(dom, "runs-body").children.length === 1, "first run row");
  $(dom, "run-runner").value = "local";
  click(dom, "run-go");
  await until(() => $(dom, "runs-body").children.length === 2, "second run row");
  assert.deepEqual([...$(dom, "runs-body").querySelectorAll("tr")].map((r) => r.children[3].textContent.trim()), ["failing", "passing"]);
  dom.window.document.querySelector('[data-tab="compare"]').dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  click(dom, "compare-go");
  await until(() => text(dom, "verdict") === "Verdict: improved", "verdict");
  assert.ok(text(dom, "diff-body").includes("message"));
  assert.ok(text(dom, "report-text").includes("RegressAgent report"));
  click(dom, "case-delete");
  await until(() => dom.window.location.hash === "#/cases", "back to the list after delete");
  await until(() => text(dom, "cases-list").includes("No cases yet."), "case removed");
});

test("dashboard: stats, health tiles with sparklines, 'needs attention' for a failing case", async () => {
  const setup = await open("#token=" + token);
  await booted(setup);
  go(setup, "/cases");
  await until(() => $(setup, "case-new"), "cases page");
  type(setup, "case-new", JSON.stringify(CASE));
  click(setup, "case-add");
  await until(() => setup.window.location.hash === "#/cases/ui-greet", "case page");
  await until(() => $(setup, "run-go"), "run button");
  $(setup, "run-runner").value = "local-legacy";
  click(setup, "run-go");
  await until(() => $(setup, "runs-body").children.length === 1, "run recorded");
  const dom = await open("#token=" + token);
  await booted(dom);
  await until(() => $(dom, "tiles") && $(dom, "tiles").children.length >= 1, "tiles");
  const tile = [...$(dom, "tiles").children].find((t) => t.textContent.includes("ui-greet"));
  assert.ok(tile.className.includes("fail") && tile.querySelector("svg.spark"));
  assert.ok(text(dom, "attention").includes("Greeting works"));
  assert.notEqual(text(dom, "stat-runs"), "0");
  assert.ok(text(dom, "recent").includes("ui-greet"));
});

test("errors and bad input are shown as readable toasts", async () => {
  const dom = await open("#token=" + token + "");
  await booted(dom);
  go(dom, "/cases");
  await until(() => $(dom, "case-new"), "cases page");
  type(dom, "case-new", "{not json");
  click(dom, "case-add");
  await until(() => toasts(dom).includes("not valid JSON"), "json error");
  type(dom, "case-new", JSON.stringify({ ...CASE, case_id: "bad", assertions: [{ path: "a..b", operator: "exists" }] }));
  click(dom, "case-add");
  await until(() => toasts(dom).includes("validation failed"), "422 shown");
  assert.ok(dom.window.document.querySelector(".toast.error"));
});

test("traces: store (redacted), open with tool-call timeline, preview and save a case", async () => {
  const dom = await open("#token=" + token);
  await booted(dom);
  go(dom, "/traces");
  await until(() => $(dom, "trace-new"), "traces page");
  const secret = "sk-live-abcdef1234567890";
  type(dom, "trace-new", JSON.stringify({
    trace_id: "ui-trace", input: { q: "x" }, output: { message: "hi", api_key: secret, n: 2 },
    steps: [{ tool: "lookup_order", arguments: { order_id: "A-1" }, result: "shipped" }, { tool: "send_reply", arguments: {} }],
  }));
  click(dom, "trace-add");
  await until(() => toasts(dom).includes("secrets were redacted"), "redaction notice");
  await until(() => dom.window.location.hash === "#/traces/ui-trace" && $(dom, "trace-detail"), "trace page");
  assert.ok(text(dom, "trace-detail").includes("[REDACTED") && !bodyText(dom).includes(secret));
  assert.ok(text(dom, "steps").includes("lookup_order") && text(dom, "steps").includes("send_reply"));
  click(dom, "trace-preview");
  await until(() => !$(dom, "trace-case-wrap").hidden, "preview");
  assert.ok(text(dom, "trace-case-table").includes("tool_order") && text(dom, "trace-case-notes").includes("api_key"));
  type(dom, "trace-case-id", "ui-from-trace");
  click(dom, "trace-save");
  await until(() => toasts(dom).includes("Saved case ui-from-trace"), "saved toast");
  go(dom, "/cases");
  await until(() => text(dom, "cases-list").includes("ui-from-trace"), "saved case listed");
});

test("hash routing, back/forward and deep links", async () => {
  const dom = await open("#token=" + token);
  await booted(dom);
  go(dom, "/cases");
  await until(() => dom.window.document.querySelector('#nav a[aria-current="page"]').dataset.nav === "cases", "cases nav");
  go(dom, "/traces");
  await until(() => dom.window.document.querySelector('#nav a[aria-current="page"]').dataset.nav === "traces", "traces nav");
  go(dom, "/cases/does-not-exist");
  await until(() => toasts(dom).includes("not found"), "missing case toast");
  await until(() => dom.window.location.hash === "#/cases", "falls back to the list");
  go(dom, "/%E0%A4%A"); // malformed escape must not crash the router
  await until(() => dom.window.document.querySelector('#nav a[aria-current="page"]'), "router survived");
});

test("command palette (Ctrl+K), keyboard shortcuts and theme toggle", async () => {
  const dom = await open("#token=" + token);
  await booted(dom);
  go(dom, "/cases");
  await until(() => $(dom, "case-new"), "cases page");
  key(dom, "k", { ctrlKey: true });
  assert.ok(!$(dom, "palette").hidden);
  await until(() => $(dom, "palette-list").children.length >= 5, "default palette items");
  type(dom, "palette-input", "traces");
  await until(() => $(dom, "palette-list").children.length === 1 && text(dom, "palette-list").includes("Go to Traces"), "filtered");
  key(dom, "Enter", {}, $(dom, "palette-input"));
  await until(() => dom.window.location.hash === "#/traces", "navigated by palette");
  assert.ok($(dom, "palette").hidden);
  key(dom, "k", { metaKey: true });
  assert.ok(!$(dom, "palette").hidden);
  key(dom, "Escape");
  assert.ok($(dom, "palette").hidden);
  key(dom, "g"); key(dom, "o");
  await until(() => dom.window.location.hash === "#/overview", "g o");
  key(dom, "g"); key(dom, "c");
  await until(() => dom.window.location.hash === "#/cases", "g c");
  key(dom, "?");
  assert.ok(!$(dom, "help").hidden);
  click(dom, "help-close");
  assert.ok($(dom, "help").hidden);
  await until(() => $(dom, "filter"), "filter box");
  key(dom, "/");
  assert.equal(dom.window.document.activeElement, $(dom, "filter"));
  key(dom, "g", {}, $(dom, "filter")); // typing in a field must not trigger shortcuts
  key(dom, "o", {}, $(dom, "filter"));
  assert.equal(dom.window.location.hash, "#/cases");
  const root = dom.window.document.documentElement;
  assert.equal(root.getAttribute("data-theme"), null);
  click(dom, "theme-toggle");
  assert.equal(root.getAttribute("data-theme"), "light");
  click(dom, "theme-toggle");
  assert.equal(root.getAttribute("data-theme"), "dark");
  assert.equal(text(dom, "theme-toggle"), "Theme: dark");
  click(dom, "theme-toggle");
  assert.equal(root.getAttribute("data-theme"), null);
});

test("server data is rendered as text, never as HTML", async () => {
  const dom = await open("#token=" + token);
  await booted(dom);
  const evil = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>';
  const evilId = '<img src=x onerror="window.__pwned=2">'; // ids may not contain "/" (API rule)
  go(dom, "/traces");
  await until(() => $(dom, "trace-new"), "traces page");
  type(dom, "trace-new", JSON.stringify({ trace_id: evilId, input: { q: evil }, output: { message: evil }, steps: [{ tool: evil.slice(0, 40), arguments: { a: evil } }] }));
  click(dom, "trace-add");
  await until(() => $(dom, "trace-detail") && text(dom, "trace-detail").includes("onerror"), "trace detail as text");
  assert.ok(text(dom, "steps").includes("onerror"));
  go(dom, "/cases");
  await until(() => $(dom, "case-new"), "cases page");
  type(dom, "case-new", JSON.stringify({ ...CASE, case_id: "ui-xss", name: evil, assertions: [{ path: "message", operator: "equals", expected: evil }] }));
  click(dom, "case-add");
  await until(() => $(dom, "assertions-body") && text(dom, "assertions-body").includes("onerror"), "case assertions as text");
  assert.ok(bodyText(dom).includes("<img"), "payload is visible as literal text");
  go(dom, "/overview");
  await until(() => $(dom, "tiles") && text(dom, "tiles").includes("onerror"), "tile name as text");
  go(dom, "/traces");
  await until(() => text(dom, "traces-list").includes("onerror"), "trace list as text");
  const doc = dom.window.document;
  assert.equal(doc.querySelectorAll("#main img, #main script, #palette img").length, 0);
  assert.equal(dom.window.__pwned, undefined);
});

test("deleting asks for confirmation and a cancel deletes nothing", async () => {
  const dom = await open("#token=" + token);
  await booted(dom);
  go(dom, "/cases");
  await until(() => $(dom, "case-new"), "cases page");
  type(dom, "case-new", JSON.stringify({ ...CASE, case_id: "ui-keep", name: "Keep me" }));
  click(dom, "case-add");
  await until(() => $(dom, "case-delete"), "case page");
  let asked = 0;
  dom.window.confirm = () => { asked += 1; return false; };
  click(dom, "case-delete");
  await new Promise((r) => setTimeout(r, 250));
  assert.equal(asked, 1);
  assert.equal(dom.window.location.hash, "#/cases/ui-keep");
  go(dom, "/cases");
  await until(() => text(dom, "cases-list").includes("ui-keep"), "case is still there");
});
