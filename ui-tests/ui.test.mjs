// End-to-end tests of the local UI: the real app.js runs in jsdom and talks to a REAL API server
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
const text = (dom, id) => $(dom, id).textContent;
const click = (dom, id) => $(dom, id).dispatchEvent(new dom.window.Event("click", { bubbles: true }));
const type = (dom, id, value) => { $(dom, id).value = value; };
const clickButtonIn = (dom, listId, label) => {
  const b = [...$(dom, listId).querySelectorAll("button")].find((x) => x.textContent === label);
  assert.ok(b, "no button " + label);
  b.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
};

const CASE = {
  case_id: "ui-greet", name: "greet",
  input: { action: "greet", name: "Ava" },
  assertions: [{ path: "message", operator: "equals", expected: "Hello, Ava!" }],
};

test("the token is taken from the URL fragment, removed from the address bar, and the empty state shows", async () => {
  const dom = await open("#token=" + token);
  await until(() => !$(dom, "app").hidden, "app visible");
  assert.equal(dom.window.location.hash, "");
  assert.ok(!dom.window.location.href.includes(token));
  await until(() => text(dom, "cases-list").includes("No cases yet."), "empty cases state");
  await until(() => text(dom, "traces-list").includes("No traces yet."), "empty traces state");
  assert.ok($(dom, "login").hidden);
});

test("without a token the login form shows; a wrong token is rejected; the right one connects", async () => {
  const dom = await open();
  await until(() => !$(dom, "login").hidden, "login form");
  assert.ok($(dom, "app").hidden);
  type(dom, "token-input", "definitely-wrong");
  click(dom, "token-save");
  await until(() => text(dom, "status").includes("token was rejected"), "rejection message");
  assert.ok($(dom, "app").hidden && !$(dom, "login").hidden);
  type(dom, "token-input", token);
  click(dom, "token-save");
  await until(() => !$(dom, "app").hidden, "app visible after the right token");
  assert.equal($(dom, "token-input").value, "");
});

test("case lifecycle: add, run twice, compare, report, delete", async () => {
  const dom = await open("#token=" + token);
  await until(() => !$(dom, "app").hidden, "app");
  type(dom, "case-new", JSON.stringify(CASE));
  click(dom, "case-add");
  await until(() => [...$(dom, "cases-list").querySelectorAll("button")].some((b) => b.textContent === "ui-greet"), "case listed");
  clickButtonIn(dom, "cases-list", "ui-greet");
  await until(() => !$(dom, "case-panel").hidden && text(dom, "case-detail").includes("Hello, Ava!"), "case detail");
  await until(() => !$(dom, "runs-empty").hidden, "no-runs state");

  type(dom, "run-runner", "local-legacy"); $(dom, "run-runner").value = "local-legacy";
  click(dom, "run-go");
  await until(() => $(dom, "runs-body").children.length === 1, "first run row");
  $(dom, "run-runner").value = "local";
  click(dom, "run-go");
  await until(() => $(dom, "runs-body").children.length === 2, "second run row");
  const cells = [...$(dom, "runs-body").querySelectorAll("tr")].map((r) => r.children[3].textContent);
  assert.deepEqual(cells, ["failed", "passed"]);

  click(dom, "compare-go");
  await until(() => text(dom, "verdict") === "Verdict: improved", "verdict");
  assert.ok(text(dom, "diff-body").includes("message"));
  assert.ok(text(dom, "report-text").includes("RegressAgent report"));

  click(dom, "case-delete");
  await until(() => !text(dom, "cases-list").includes("ui-greet"), "case removed");
  assert.ok($(dom, "case-panel").hidden);
});

test("API errors and bad input are shown as readable messages", async () => {
  const dom = await open("#token=" + token);
  await until(() => !$(dom, "app").hidden, "app");
  type(dom, "case-new", "{not json");
  click(dom, "case-add");
  await until(() => text(dom, "status").includes("not valid JSON"), "json error");
  type(dom, "case-new", JSON.stringify({ ...CASE, case_id: "bad", assertions: [{ path: "a..b", operator: "exists" }] }));
  click(dom, "case-add");
  await until(() => text(dom, "status").includes("validation failed"), "422 shown");
  assert.ok($(dom, "status").className.includes("error"));
});

test("traces: store (redacted), open, preview and save a case", async () => {
  const dom = await open("#token=" + token);
  await until(() => !$(dom, "app").hidden, "app");
  const secret = "sk-live-abcdef1234567890";
  type(dom, "trace-new", JSON.stringify({ trace_id: "ui-trace", input: { q: "x" }, output: { message: "hi", api_key: secret, n: 2 } }));
  click(dom, "trace-add");
  await until(() => text(dom, "status").includes("secrets were redacted"), "redaction notice");
  clickButtonIn(dom, "traces-list", "ui-trace");
  await until(() => !$(dom, "trace-panel").hidden && text(dom, "trace-detail").includes("[REDACTED"), "trace detail");
  assert.ok(!text(dom, "trace-detail").includes(secret));
  click(dom, "trace-preview");
  await until(() => !$(dom, "trace-case-out").hidden && text(dom, "trace-case-out").includes('"operator": "equals"'), "preview");
  assert.ok(text(dom, "trace-case-out").includes("api_key"));
  type(dom, "trace-case-id", "ui-from-trace");
  click(dom, "trace-save");
  await until(() => [...$(dom, "cases-list").querySelectorAll("button")].some((b) => b.textContent === "ui-from-trace"), "saved case listed");
});

test("server data is rendered as text, never as HTML", async () => {
  const dom = await open("#token=" + token);
  await until(() => !$(dom, "app").hidden, "app");
  const evil = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>';
  const evilId = '<img src=x onerror="window.__pwned=1">'; // ids may not contain "/" (API rule)
  type(dom, "trace-new", JSON.stringify({ trace_id: evilId, input: { q: evil }, output: { message: evil } }));
  click(dom, "trace-add");
  await until(() => [...$(dom, "traces-list").querySelectorAll("button")].some((b) => b.textContent === evilId), "evil id listed as text");
  clickButtonIn(dom, "traces-list", evilId);
  await until(() => text(dom, "trace-detail").includes("onerror"), "detail shown as text");
  type(dom, "case-new", JSON.stringify({ ...CASE, case_id: "ui-xss", name: evil }));
  click(dom, "case-add");
  await until(() => [...$(dom, "cases-list").querySelectorAll("button")].some((b) => b.textContent === "ui-xss"), "xss case listed");
  clickButtonIn(dom, "cases-list", "ui-xss");
  await until(() => text(dom, "case-detail").includes("onerror"), "case detail as text");
  const doc = dom.window.document;
  assert.equal(doc.querySelectorAll("#app img, #app script").length, 0);
  assert.equal(dom.window.__pwned, undefined);
});
