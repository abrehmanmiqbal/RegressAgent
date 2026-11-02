"use strict";
// RegressAgent local UI. Plain DOM, no framework, no inline scripts. Everything that comes from the
// server is rendered with textContent / createTextNode (never raw HTML). The API token lives in
// memory only: it is read from the URL fragment once, then removed from the address bar.
(() => {
  let token = null;
  let currentCase = null;
  let currentTrace = null;
  const $ = (id) => document.getElementById(id);

  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (key === "text") node.textContent = value;
      else if (key === "onclick") node.addEventListener("click", value);
      else node.setAttribute(key, value);
    }
    for (const child of children) node.append(child);
    return node;
  }
  const clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); };
  const pretty = (value) => JSON.stringify(value, null, 2);

  function status(text, isError) {
    const node = $("status");
    node.textContent = text || "";
    node.className = isError ? "error" : "";
  }

  async function api(method, path, body) {
    const headers = { Authorization: "Bearer " + token };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let response;
    try {
      response = await fetch("/api/v1" + path, {
        method, headers, body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (err) {
      throw new Error("cannot reach the API");
    }
    let data = null;
    try { data = await response.json(); } catch (err) { /* empty body */ }
    if (!response.ok) {
      const detail = data && data.details ? ": " + data.details.map((d) => d.loc + " " + d.msg).join("; ") : "";
      if (response.status === 401) {
        showLogin("The token was rejected. Paste it again.");
        throw new Error("The token was rejected. Paste it again.");
      }
      throw new Error(((data && data.error) || "HTTP " + response.status) + detail);
    }
    return data;
  }

  async function guarded(task, loadingText) {
    const marker = loadingText || "Working...";
    status(marker);
    try {
      const result = await task();
      if ($("status").textContent === marker) status(""); // keep any message the task itself set
      return result;
    } catch (err) {
      status(err.message, true);
      return undefined;
    }
  }

  function showLogin(message) {
    token = null;
    $("app").hidden = true;
    $("login").hidden = false;
    if (message) status(message, true);
  }

  function showApp() {
    $("login").hidden = true;
    $("app").hidden = false;
    refreshCases();
    refreshTraces();
  }

  function listItems(listId, ids, emptyText, onOpen) {
    const list = $(listId);
    clear(list);
    if (!ids.length) { list.append(el("li", { text: emptyText })); return; }
    for (const id of ids) list.append(el("li", {}, el("button", { type: "button", text: id, onclick: () => onOpen(id) })));
  }

  // ---------------------------------------------------------------- cases
  const refreshCases = () => guarded(async () => {
    listItems("cases-list", await api("GET", "/cases"), "No cases yet.", openCase);
  }, "Loading cases...");

  async function openCase(id) {
    await guarded(async () => {
      const data = await api("GET", "/cases/" + encodeURIComponent(id));
      currentCase = id;
      $("case-title").textContent = id;
      $("case-detail").textContent = pretty(data);
      $("case-panel").hidden = false;
      $("compare-out").hidden = true;
      await refreshRuns();
    }, "Loading case...");
  }

  async function refreshRuns() {
    const runs = await api("GET", "/cases/" + encodeURIComponent(currentCase) + "/runs");
    const body = $("runs-body");
    clear(body);
    $("runs-empty").hidden = runs.length > 0;
    $("runs-table").hidden = runs.length === 0;
    for (const run of runs) {
      body.append(el("tr", {},
        el("td", { text: String(run.run_id) }), el("td", { text: run.label }), el("td", { text: run.runner }),
        el("td", { text: run.passed ? "passed" : "failed", class: run.passed ? "ok" : "bad" }),
        el("td", { text: run.created_at })));
    }
    for (const id of ["cmp-before", "cmp-after"]) {
      const select = $(id);
      clear(select);
      for (const run of runs) select.append(el("option", { value: String(run.run_id), text: "#" + run.run_id + " " + run.label }));
    }
    if (runs.length > 1) $("cmp-before").selectedIndex = 0, $("cmp-after").selectedIndex = runs.length - 1;
  }

  async function addCase() {
    await guarded(async () => {
      let data;
      try { data = JSON.parse($("case-new").value); } catch (err) { throw new Error("the case is not valid JSON"); }
      const flag = $("case-overwrite").checked ? "?overwrite=1" : "";
      const saved = await api("POST", "/cases" + flag, data);
      $("case-new").value = "";
      await refreshCases();
      status("Saved case " + saved.case_id);
    }, "Saving...");
  }

  async function runCase() {
    if (!currentCase) return;
    await guarded(async () => {
      const result = await api("POST", "/cases/" + encodeURIComponent(currentCase) + "/run", {
        label: $("run-label").value, runner: $("run-runner").value, repeat: Number($("run-repeat").value),
      });
      await refreshRuns();
      status("Run " + result.run_ids.join(", ") + (result.passed ? " passed" : " failed"));
    }, "Running...");
  }

  async function deleteCase() {
    if (!currentCase || !window.confirm("Delete case " + currentCase + " and all its runs?")) return;
    await guarded(async () => {
      await api("DELETE", "/cases/" + encodeURIComponent(currentCase) + "?confirm=yes");
      currentCase = null;
      $("case-panel").hidden = true;
      await refreshCases();
    }, "Deleting...");
  }

  async function compareRuns() {
    const before = $("cmp-before").value;
    const after = $("cmp-after").value;
    if (!before || !after) { status("Pick two runs first (run the case at least twice).", true); return; }
    await guarded(async () => {
      const q = "before=" + encodeURIComponent(before) + "&after=" + encodeURIComponent(after);
      const [comparison, report] = await Promise.all([api("GET", "/compare?" + q), api("GET", "/report?" + q)]);
      $("verdict").textContent = "Verdict: " + comparison.verdict;
      const body = $("diff-body");
      clear(body);
      for (const diff of comparison.diffs) {
        body.append(el("tr", {}, el("td", { text: diff.path }), el("td", { text: diff.operator }),
          el("td", { text: diff.change, class: diff.change === "regressed" ? "bad" : "" })));
      }
      $("report-text").textContent = report.text;
      $("compare-out").hidden = false;
    }, "Comparing...");
  }

  // ---------------------------------------------------------------- traces
  const refreshTraces = () => guarded(async () => {
    listItems("traces-list", await api("GET", "/traces"), "No traces yet.", openTrace);
  }, "Loading traces...");

  async function openTrace(id) {
    await guarded(async () => {
      const data = await api("GET", "/traces/" + encodeURIComponent(id));
      currentTrace = id;
      $("trace-title").textContent = id;
      $("trace-detail").textContent = pretty(data);
      $("trace-case-id").value = id;
      $("trace-case-out").hidden = true;
      $("trace-panel").hidden = false;
    }, "Loading trace...");
  }

  async function addTrace() {
    await guarded(async () => {
      let data;
      try { data = JSON.parse($("trace-new").value); } catch (err) { throw new Error("the trace is not valid JSON"); }
      const flag = $("trace-overwrite").checked ? "?overwrite=1" : "";
      const saved = await api("POST", "/traces" + flag, data);
      $("trace-new").value = "";
      await refreshTraces();
      status("Stored trace " + saved.trace_id + (saved.redacted ? " (secrets were redacted)" : ""));
    }, "Saving...");
  }

  async function traceToCase(save) {
    if (!currentTrace) return;
    await guarded(async () => {
      const body = { save };
      if ($("trace-case-id").value) body.case_id = $("trace-case-id").value;
      const result = await api("POST", "/traces/" + encodeURIComponent(currentTrace) + "/case" + (save ? "?overwrite=1" : ""), body);
      $("trace-case-out").textContent = pretty(result.case) + (result.notes.length ? "\n\nNotes:\n- " + result.notes.join("\n- ") : "");
      $("trace-case-out").hidden = false;
      if (save) { await refreshCases(); status("Saved case " + result.case.case_id); }
    }, "Working...");
  }

  // ---------------------------------------------------------------- wiring
  function switchTab(name) {
    $("tab-cases").hidden = name !== "cases";
    $("tab-traces").hidden = name !== "traces";
    for (const button of document.querySelectorAll("nav button")) button.classList.toggle("active", button.dataset.tab === name);
  }

  function init() {
    for (const button of document.querySelectorAll("nav button")) button.addEventListener("click", () => switchTab(button.dataset.tab));
    $("case-add").addEventListener("click", addCase);
    $("run-go").addEventListener("click", runCase);
    $("case-delete").addEventListener("click", deleteCase);
    $("compare-go").addEventListener("click", compareRuns);
    $("trace-add").addEventListener("click", addTrace);
    $("trace-preview").addEventListener("click", () => traceToCase(false));
    $("trace-save").addEventListener("click", () => traceToCase(true));
    $("token-save").addEventListener("click", () => {
      token = $("token-input").value.trim();
      $("token-input").value = "";
      if (token) { status(""); showApp(); }
    });
    const match = /(?:^|[#&])token=([^&]+)/.exec(window.location.hash);
    if (match) {
      token = decodeURIComponent(match[1]);
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
      showApp();
    } else {
      showLogin("");
    }
  }

  init();
})();
