"use strict";
// RegressAgent local UI v2. Plain DOM, no framework, no inline scripts or styles. Everything that
// comes from the server is rendered with textContent / createTextNode (never raw HTML). The API
// token lives in memory only: it is read from the URL fragment once, then removed from the address
// bar. Routing uses the fragment (#/cases/ID), so back/forward and deep links work.
(() => {
  const SVG_NS = "http://www.w3.org/2000/svg";
  let token = null;
  let seq = 0; // render sequence: a slow response for an old route must not paint over a newer one
  let theme = 0;
  const cache = { cases: [], traces: [] };
  const THEMES = ["auto", "light", "dark"];
  const $ = (id) => document.getElementById(id);
  const main = () => $("main");

  // ---------------------------------------------------------------- tiny DOM toolkit
  function el(tag, props, ...kids) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "text") node.textContent = value;
      else if (key === "onclick") node.addEventListener("click", value);
      else if (key === "oninput") node.addEventListener("input", value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const kid of kids) if (kid !== null && kid !== undefined && kid !== false) node.append(kid);
    return node;
  }
  function svg(tag, attrs, ...kids) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, String(value));
    for (const kid of kids) node.append(kid);
    return node;
  }
  const ICONS = {
    shield: "M12 3l7 3v5c0 5-3 8-7 10-4-2-7-5-7-10V6z M9 12l2 2 4-4",
    check: "M5 12l5 5 9-10",
    cross: "M6 6l12 12M18 6L6 18",
    play: "M8 5v14l11-7z",
    trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
    arrow: "M5 12h14M13 6l6 6-6 6",
    copy: "M9 9h10v10H9z M5 15V5h10",
    plus: "M12 5v14M5 12h14",
  };
  function icon(name, size = 16) {
    return svg("svg", { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" },
      svg("path", { d: ICONS[name] }));
  }
  const clear = (node) => node.replaceChildren();
  const pretty = (value) => JSON.stringify(value, null, 2);
  const short = (value, max = 220) => {
    let text = typeof value === "string" ? value : JSON.stringify(value);
    if (text === undefined) text = "undefined";
    return text.length > max ? text.slice(0, max) + "…" : text;
  };
  const when = (iso) => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString(); };
  const pill = (text, kind) => el("span", { class: "pill " + (kind || "neutral"), text });
  const statusPill = (passed) => el("span", { class: "pill " + (passed ? "ok" : "bad") }, icon(passed ? "check" : "cross", 12), document.createTextNode(passed ? "passing" : "failing"));
  const button = (label, onclick, cls, iconName, extra) => el("button", { type: "button", class: "btn " + (cls || ""), onclick, ...(extra || {}) }, iconName ? icon(iconName) : null, document.createTextNode(label));
  const skeleton = () => el("div", { class: "card", "aria-hidden": "true" }, el("div", { class: "skeleton" }), el("div", { class: "skeleton" }), el("div", { class: "skeleton" }));

  function emptyState(title, text, ...actions) {
    const art = svg("svg", { viewBox: "0 0 96 96", fill: "none", "aria-hidden": "true" },
      svg("rect", { x: 14, y: 22, width: 68, height: 52, rx: 10, stroke: "currentColor", "stroke-width": 3, opacity: 0.5 }),
      svg("path", { d: "M14 36h68", stroke: "currentColor", "stroke-width": 3, opacity: 0.5 }),
      svg("circle", { cx: 48, cy: 56, r: 9, stroke: "currentColor", "stroke-width": 3 }));
    return el("div", { class: "empty" }, art, el("h3", { text: title }), el("p", { text }), el("div", { class: "row" }, ...actions));
  }

  function sparkline(history, total = 20) {
    const root = svg("svg", { class: "spark", width: total * 6, height: 24, viewBox: "0 0 " + total * 6 + " 24", role: "img", "aria-label": "last " + history.length + " runs" });
    const offset = total - history.length;
    for (let i = 0; i < total; i += 1) {
      const entry = i < offset ? null : history[i - offset];
      root.append(svg("rect", { class: entry === null ? "n" : entry ? "p" : "f", x: i * 6, y: entry === null ? 20 : entry ? 4 : 10, width: 4, height: entry === null ? 4 : entry ? 20 : 14, rx: 1.5 }));
    }
    return root;
  }

  // ---------------------------------------------------------------- feedback
  function toast(message, kind) {
    const node = el("div", { class: "toast " + (kind || ""), text: message });
    $("toasts").append(node);
    setTimeout(() => node.remove(), kind === "error" ? 9000 : 5000);
  }
  async function busy(btn, task) {
    if (btn) btn.disabled = true;
    try { return await task(); } finally { if (btn) btn.disabled = false; }
  }
  async function load(task) {
    try { return await task(); } catch (err) { toast(err.message, "error"); return undefined; }
  }

  // ---------------------------------------------------------------- API
  async function api(method, path, body) {
    const headers = { Authorization: "Bearer " + token };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let response;
    try {
      response = await fetch("/api/v1" + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    } catch (err) {
      $("conn").textContent = "offline";
      throw new Error("cannot reach the API");
    }
    $("conn").textContent = "connected";
    let data = null;
    try { data = await response.json(); } catch (err) { /* empty body */ }
    if (!response.ok) {
      if (response.status === 401) {
        showLogin("The token was rejected. Paste it again.");
        throw new Error("The token was rejected. Paste it again.");
      }
      const detail = data && data.details ? ": " + data.details.map((d) => d.loc + " " + d.msg).join("; ") : "";
      throw new Error(((data && data.error) || "HTTP " + response.status) + detail);
    }
    return data;
  }

  // ---------------------------------------------------------------- shell, routing, theme
  function showLogin(message) {
    token = null;
    seq += 1;
    $("shell").hidden = true;
    $("login").hidden = false;
    if (message) toast(message, "error");
    $("token-input").focus();
  }
  function showApp() {
    $("login").hidden = true;
    $("shell").hidden = false;
    route();
    load(async () => { cache.traces = await api("GET", "/traces"); });
  }
  function navigate(path) { window.location.hash = "#" + path; }
  function route() {
    if (!token) return;
    let parts;
    try { parts = window.location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent); } catch (err) { parts = ["overview"]; }
    const [view, id] = parts;
    const section = ["cases", "traces"].includes(view) ? view : "overview";
    for (const link of document.querySelectorAll("#nav a")) {
      if (link.dataset.nav === section) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
    }
    if (section === "cases") (id ? renderCase(id) : renderCases());
    else if (section === "traces") (id ? renderTrace(id) : renderTraces());
    else renderOverview();
  }
  function setTheme(index) {
    theme = index % THEMES.length;
    if (THEMES[theme] === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", THEMES[theme]);
    $("theme-toggle").textContent = "Theme: " + THEMES[theme];
  }
  function page(title, subtitle, ...actions) {
    return el("div", { class: "row between" }, el("div", {}, el("h1", { text: title }), subtitle ? el("div", { class: "muted", text: subtitle }) : null), el("div", { class: "row" }, ...actions));
  }
  const mount = (...nodes) => { main().replaceChildren(...nodes); };

  // ---------------------------------------------------------------- overview
  const DEMO_CASE = {
    case_id: "demo-greet", name: "Demo: the agent greets by name",
    input: { action: "greet", name: "Ava" },
    assertions: [
      { path: "message", operator: "equals", expected: "Hello, Ava!" },
      { path: "action", operator: "equals", expected: "greet" },
    ],
  };
  async function loadDemo(btn) {
    await busy(btn, () => load(async () => {
      await api("POST", "/cases?overwrite=1", DEMO_CASE);
      toast("Demo case created. Run it with both runners, then compare.", "ok");
      navigate("/cases/demo-greet");
    }));
  }
  async function renderOverview() {
    const mine = ++seq;
    mount(page("Overview", "Is your agent still behaving?"), skeleton(), skeleton());
    const data = await load(() => api("GET", "/overview"));
    if (mine !== seq || !data) return;
    cache.cases = data.cases;
    const c = data.counts;
    const rate = data.cases.reduce((a, x) => a + x.runs, 0) ? Math.round((100 * data.cases.reduce((a, x) => a + x.passed_runs, 0)) / data.cases.reduce((a, x) => a + x.runs, 0)) + "%" : "–";
    const stat = (num, label, id) => el("div", { class: "card stat" }, el("div", { class: "num", id, text: String(num) }), el("div", { class: "label", text: label }));
    const parts = [page("Overview", "Is your agent still behaving?")];
    if (c.cases === 0 && c.traces === 0) {
      parts.push(el("div", { class: "card hero", id: "onboarding" },
        el("h2", { text: "Welcome. Let's catch your first regression." }),
        el("ol", { class: "steps" },
          el("li", { text: "Add a case (what the agent should do) or store a captured trace." }),
          el("li", { text: "Run it, change your agent, run it again." }),
          el("li", { text: "Compare the runs: RegressAgent shows exactly which assertion regressed." })),
        el("div", { class: "row" },
          button("Load the demo case", (e) => loadDemo(e.currentTarget), "primary", "play", { id: "demo-load" }),
          button("Add your own case", () => navigate("/cases"), "", "plus"))));
    }
    parts.push(el("div", { class: "grid stats" }, stat(c.cases, "Cases", "stat-cases"), stat(c.traces, "Stored traces", "stat-traces"), stat(c.runs, "Recorded runs", "stat-runs"), stat(rate, "Pass rate (all runs)", "stat-rate")));
    const failing = data.cases.filter((x) => x.history.length && !x.history[x.history.length - 1]);
    parts.push(el("div", { class: "card", id: "attention" }, el("h2", { text: "Needs attention" }),
      failing.length ? el("ul", { class: "list" }, ...failing.map((x) => el("li", {}, el("a", { href: "#/cases/" + encodeURIComponent(x.case_id) }, el("span", { text: x.name }), statusPill(false)))))
        : el("p", { class: "muted", text: data.cases.some((x) => x.history.length) ? "All clear: every case's latest run is passing." : "No runs yet." })));
    if (data.cases.length) {
      parts.push(el("div", { class: "card" }, el("h2", { text: "Case health" }),
        el("div", { class: "grid tiles", id: "tiles" }, ...data.cases.map((x) => {
          const last = x.history.length ? x.history[x.history.length - 1] : null;
          return el("a", { class: "tile " + (last === null ? "" : last ? "pass" : "fail"), href: "#/cases/" + encodeURIComponent(x.case_id) },
            el("div", { class: "title", text: x.name }), el("div", { class: "muted mono", text: x.case_id }),
            el("div", { class: "meta" }, sparkline(x.history), el("span", { text: x.runs ? x.passed_runs + "/" + x.runs + " passed" : "no runs" })));
        }))));
    }
    if (data.recent.length) {
      parts.push(el("div", { class: "card" }, el("h2", { text: "Recent runs" }),
        el("ul", { class: "list", id: "recent" }, ...data.recent.map((r) => el("li", {}, el("a", { href: "#/cases/" + encodeURIComponent(r.case_id) },
          el("span", {}, el("strong", { text: "#" + r.run_id + " " }), document.createTextNode(r.case_id + " · " + r.label + " · " + r.runner)),
          el("span", { class: "row" }, el("span", { class: "muted", text: when(r.created_at) }), statusPill(r.passed))))))));
    }
    mount(...parts);
  }

  // ---------------------------------------------------------------- cases list
  function filterBox(onInput) {
    return el("input", { id: "filter", type: "search", placeholder: "Filter…  (press /)", "aria-label": "Filter", oninput: (e) => onInput(e.target.value.toLowerCase()) });
  }
  async function renderCases() {
    const mine = ++seq;
    mount(page("Cases", "What your agent must keep doing"), skeleton());
    const data = await load(() => api("GET", "/overview"));
    if (mine !== seq || !data) return;
    cache.cases = data.cases;
    const list = el("ul", { class: "list", id: "cases-list" });
    const draw = (needle) => {
      clear(list);
      const rows = data.cases.filter((x) => (x.case_id + " " + x.name).toLowerCase().includes(needle));
      if (!rows.length) { list.append(el("li", { class: "empty", text: data.cases.length ? "No case matches the filter." : "No cases yet." })); return; }
      for (const x of rows) {
        const last = x.history.length ? x.history[x.history.length - 1] : null;
        list.append(el("li", {}, el("a", { href: "#/cases/" + encodeURIComponent(x.case_id) },
          el("span", {}, el("strong", { text: x.case_id }), el("span", { class: "muted", text: "  " + x.name })),
          el("span", { class: "row" }, sparkline(x.history), last === null ? pill("no runs") : statusPill(last)))));
      }
    };
    draw("");
    const newCase = el("details", { class: "card" }, el("summary", { text: "Add a case (JSON)" }),
      el("textarea", { id: "case-new", rows: 9, spellcheck: "false", "aria-label": "Case JSON", placeholder: '{"case_id": "...", "name": "...", "input": {}, "assertions": [{"path": "...", "operator": "equals", "expected": "..."}]}' }),
      el("div", { class: "row between" }, el("label", {}, el("input", { type: "checkbox", id: "case-overwrite" }), document.createTextNode(" overwrite if it exists")),
        button("Save case", (e) => addCase(e.currentTarget), "primary", "plus", { id: "case-add" })));
    mount(page("Cases", "What your agent must keep doing", filterBox(draw)), el("div", { class: "card" }, list), newCase);
  }
  async function addCase(btn) {
    await busy(btn, () => load(async () => {
      let data;
      try { data = JSON.parse($("case-new").value); } catch (err) { throw new Error("the case is not valid JSON"); }
      const saved = await api("POST", "/cases" + ($("case-overwrite").checked ? "?overwrite=1" : ""), data);
      toast("Saved case " + saved.case_id, "ok");
      navigate("/cases/" + encodeURIComponent(saved.case_id));
    }));
  }

  // ---------------------------------------------------------------- one case
  async function renderCase(id) {
    const mine = ++seq;
    mount(page(id), skeleton(), skeleton());
    const loaded = await load(() => Promise.all([api("GET", "/cases/" + encodeURIComponent(id)), api("GET", "/cases/" + encodeURIComponent(id) + "/runs")]));
    if (mine !== seq || !loaded) { if (mine === seq) navigate("/cases"); return; }
    const [definition, runs] = loaded;
    const body = { runs, tab: runs.length ? "runs" : "assertions" };

    const header = page(definition.name, id);
    const actions = el("div", { class: "card" }, el("div", { class: "row" },
      el("label", { class: "field" }, el("span", { text: "Runner" }), el("select", { id: "run-runner" }, el("option", { text: "local" }), el("option", { text: "local-legacy" }))),
      el("label", { class: "field" }, el("span", { text: "Repeat" }), el("input", { id: "run-repeat", type: "number", min: 1, max: 20, value: 1 })),
      el("label", { class: "field" }, el("span", { text: "Label" }), el("input", { id: "run-label", value: "ui" })),
      button("Run", (e) => runCase(id, e.currentTarget, body), "primary", "play", { id: "run-go" }),
      button("Delete case", () => deleteCase(id), "danger", "trash", { id: "case-delete" })),
      el("p", { class: "muted", text: "The UI can only start the built-in demo runners; use the CLI for real agents." }));

    const tabsBar = el("div", { class: "tabs", role: "tablist" });
    const panels = {};
    const tabDefs = [["assertions", "Assertions"], ["runs", "Runs"], ["compare", "Compare"], ["definition", "Definition"]];
    const selectTab = (name) => {
      for (const [key] of tabDefs) {
        panels[key].hidden = key !== name;
        tabsBar.querySelector('[data-tab="' + key + '"]').setAttribute("aria-selected", String(key === name));
      }
    };
    for (const [key, label] of tabDefs) {
      tabsBar.append(el("button", { type: "button", role: "tab", "data-tab": key, "aria-selected": "false", text: label, onclick: () => selectTab(key) }));
      panels[key] = el("div", { class: "card", id: "tab-" + key, role: "tabpanel", hidden: true });
    }
    panels.assertions.append(el("table", {}, el("thead", {}, el("tr", {}, el("th", { text: "Path" }), el("th", { text: "Operator" }), el("th", { text: "Expected" }))),
      el("tbody", { id: "assertions-body" }, ...definition.assertions.map((a) => el("tr", {}, el("td", { class: "mono", text: a.path }), el("td", {}, pill(a.operator, "neutral")), el("td", { class: "mono", text: short(a.expected === undefined ? "" : a.expected) }))))));
    panels.definition.append(el("pre", { id: "case-detail", text: pretty(definition) }));
    const sparkHolder = el("div", { id: "spark-holder" });
    const runsEmpty = el("p", { id: "runs-empty", class: "muted", text: "No runs yet. Pick a runner above and press Run." });
    const runsBody = el("tbody", { id: "runs-body" });
    const runsTable = el("table", { id: "runs-table" }, el("thead", {}, el("tr", {}, ["Run", "Label", "Runner", "Result", "When"].map((t) => el("th", { text: t })))), runsBody);
    panels.runs.append(sparkHolder, runsEmpty, runsTable);
    const before = el("select", { id: "cmp-before", "aria-label": "Before run" });
    const after = el("select", { id: "cmp-after", "aria-label": "After run" });
    const out = el("div", { id: "compare-out", hidden: true });
    panels.compare.append(el("div", { class: "row" }, el("label", { class: "field" }, el("span", { text: "Before" }), before), el("label", { class: "field" }, el("span", { text: "After" }), after),
      button("Compare", (e) => compareRuns(before.value, after.value, out, e.currentTarget), "primary", "arrow", { id: "compare-go" })), out);
    body.refresh = (list) => {
      body.runs = list;
      clear(runsBody); clear(before); clear(after); clear(sparkHolder);
      runsEmpty.hidden = list.length > 0; runsTable.hidden = list.length === 0;
      sparkHolder.append(sparkline(list.slice(-20).map((r) => r.passed)));
      for (const r of list) {
        runsBody.append(el("tr", {}, el("td", { text: String(r.run_id) }), el("td", { text: r.label }), el("td", { text: r.runner }), el("td", {}, statusPill(r.passed)), el("td", { text: when(r.created_at) })));
        before.append(el("option", { value: String(r.run_id), text: "#" + r.run_id + " " + r.label }));
        after.append(el("option", { value: String(r.run_id), text: "#" + r.run_id + " " + r.label }));
      }
      if (list.length > 1) { before.selectedIndex = list.length - 2; after.selectedIndex = list.length - 1; }
    };
    body.refresh(runs);
    mount(header, actions, tabsBar, panels.assertions, panels.runs, panels.compare, panels.definition);
    selectTab(body.tab);
  }
  async function runCase(id, btn, body) {
    await busy(btn, () => load(async () => {
      const result = await api("POST", "/cases/" + encodeURIComponent(id) + "/run", { label: $("run-label").value, runner: $("run-runner").value, repeat: Number($("run-repeat").value) });
      body.refresh(await api("GET", "/cases/" + encodeURIComponent(id) + "/runs"));
      toast("Run " + result.run_ids.map((n) => "#" + n).join(", ") + (result.passed ? " passed" : " failed"), result.passed ? "ok" : "error");
    }));
  }
  async function deleteCase(id) {
    if (!window.confirm("Delete case " + id + " and all its runs?")) return;
    await load(async () => { await api("DELETE", "/cases/" + encodeURIComponent(id) + "?confirm=yes"); toast("Deleted " + id, "ok"); navigate("/cases"); });
  }
  const VERDICTS = { improved: ["Improved", "check"], regressed: ["Regressed", "cross"], unchanged: ["Unchanged", "arrow"] };
  async function compareRuns(beforeId, afterId, out, btn) {
    if (!beforeId || !afterId) { toast("Run the case at least twice, then compare.", "error"); return; }
    await busy(btn, () => load(async () => {
      const q = "before=" + encodeURIComponent(beforeId) + "&after=" + encodeURIComponent(afterId);
      const [cmp, report] = await Promise.all([api("GET", "/compare?" + q), api("GET", "/report?" + q)]);
      const [label, ic] = VERDICTS[cmp.verdict] || [cmp.verdict, "arrow"];
      const counts = {};
      for (const d of cmp.diffs) counts[d.change] = (counts[d.change] || 0) + 1;
      const diffs = el("div", { class: "diffs", id: "diff-body" }, ...cmp.diffs.map((d) => {
        const kids = [el("div", {}, el("strong", { class: "mono", text: d.path }), document.createTextNode("  "), pill(d.operator)), pill(d.change.replace("_", " "), d.change === "regressed" ? "bad" : d.change === "improved" ? "ok" : "neutral")];
        if ("before_actual" in d || "after_actual" in d) kids.push(el("div", { class: "vals" }, el("div", { text: "before: " + short(d.before_actual) }), el("div", { text: "after: " + short(d.after_actual) })));
        return el("div", { class: "diff " + d.change }, ...kids);
      }));
      const copy = button("Copy report", async () => {
        try { await navigator.clipboard.writeText(report.text); toast("Report copied", "ok"); } catch (err) { toast("Copy is not available here", "error"); }
      }, "", "copy");
      clear(out);
      out.append(el("div", { class: "verdict " + cmp.verdict },
        el("span", { class: "big" }, icon(ic, 22)), el("div", {}, el("div", { class: "big", id: "verdict", text: "Verdict: " + cmp.verdict }), el("div", { class: "muted", text: label + " · " + Object.entries(counts).map(([k, v]) => v + " " + k.replace("_", " ")).join(", ") }))),
        diffs, el("details", {}, el("summary", { text: "Markdown report" }), el("div", { class: "row" }, copy), el("pre", { id: "report-text", text: report.text })));
      out.hidden = false;
    }));
  }

  // ---------------------------------------------------------------- traces
  async function renderTraces() {
    const mine = ++seq;
    mount(page("Traces", "Captured behaviour (stored redacted)"), skeleton());
    const ids = await load(() => api("GET", "/traces"));
    if (mine !== seq || !ids) return;
    cache.traces = ids;
    const list = el("ul", { class: "list", id: "traces-list" });
    const draw = (needle) => {
      clear(list);
      const rows = ids.filter((x) => x.toLowerCase().includes(needle));
      if (!rows.length) { list.append(el("li", { class: "empty", text: ids.length ? "No trace matches the filter." : "No traces yet." })); return; }
      for (const id of rows) list.append(el("li", {}, el("a", { href: "#/traces/" + encodeURIComponent(id) }, el("span", { class: "mono", text: id }), icon("arrow"))));
    };
    draw("");
    const add = el("details", { class: "card" }, el("summary", { text: "Store a trace (JSON)" }),
      el("textarea", { id: "trace-new", rows: 9, spellcheck: "false", "aria-label": "Trace JSON", placeholder: '{"trace_id": "...", "input": {}, "output": {}, "steps": [{"tool": "...", "arguments": {}}]}' }),
      el("div", { class: "row between" }, el("label", {}, el("input", { type: "checkbox", id: "trace-overwrite" }), document.createTextNode(" overwrite if it exists")),
        button("Store trace (redacted)", (e) => addTrace(e.currentTarget), "primary", "plus", { id: "trace-add" })));
    mount(page("Traces", "Captured behaviour (stored redacted)", filterBox(draw)), el("div", { class: "card" }, list), add);
  }
  async function addTrace(btn) {
    await busy(btn, () => load(async () => {
      let data;
      try { data = JSON.parse($("trace-new").value); } catch (err) { throw new Error("the trace is not valid JSON"); }
      const saved = await api("POST", "/traces" + ($("trace-overwrite").checked ? "?overwrite=1" : ""), data);
      toast("Stored trace " + saved.trace_id + (saved.redacted ? " (secrets were redacted)" : ""), "ok");
      navigate("/traces/" + encodeURIComponent(saved.trace_id));
    }));
  }
  async function renderTrace(id) {
    const mine = ++seq;
    mount(page(id), skeleton());
    const trace = await load(() => api("GET", "/traces/" + encodeURIComponent(id)));
    if (mine !== seq || !trace) { if (mine === seq) navigate("/traces"); return; }
    const steps = trace.steps.length ? el("div", { class: "card", id: "steps" }, el("h2", { text: "Tool calls (" + trace.steps.length + ")" }),
      el("div", { class: "timeline" }, ...trace.steps.map((s, i) => el("div", { class: "step" },
        el("div", { class: "row" }, pill("#" + (i + 1)), el("strong", { class: "mono", text: s.tool })),
        Object.keys(s.arguments).length ? el("table", {}, el("tbody", {}, ...Object.entries(s.arguments).map(([k, v]) => el("tr", {}, el("td", { class: "mono", text: k }), el("td", { class: "mono", text: short(v) }))))) : el("div", { class: "muted", text: "no arguments" }),
        s.result !== null && s.result !== undefined ? el("div", { class: "muted mono", text: "result: " + short(s.result, 300) }) : null)))) : null;
    const out = el("div", { id: "trace-case-wrap", hidden: true }, el("div", { id: "trace-case-table" }), el("ul", { id: "trace-case-notes" }), el("details", {}, el("summary", { text: "Raw case JSON" }), el("pre", { id: "trace-case-out" })));
    const gen = el("div", { class: "card" }, el("h2", { text: "Turn this trace into a case" }),
      el("div", { class: "row" }, el("label", { class: "field" }, el("span", { text: "Case id" }), el("input", { id: "trace-case-id", value: id })),
        button("Preview case", () => traceToCase(id, false, out), "", "arrow", { id: "trace-preview" }),
        button("Save case", () => traceToCase(id, true, out), "primary", "check", { id: "trace-save" }),
        button("Delete trace", () => deleteTrace(id), "danger", "trash", { id: "trace-delete" })),
      el("p", { class: "muted", text: "Generated assertions pin exact values: review them. Secret-looking values are skipped." }), out);
    mount(page(id, "Stored trace (secrets already redacted)"), el("div", { class: "grid stats" },
      el("div", { class: "card" }, el("h3", { text: "Input" }), el("pre", { id: "trace-input", text: pretty(trace.input) })),
      el("div", { class: "card" }, el("h3", { text: "Output" }), el("pre", { id: "trace-detail", text: pretty(trace.output) }))), steps, gen);
  }
  async function traceToCase(id, save, out) {
    await load(async () => {
      const body = { save };
      if ($("trace-case-id").value) body.case_id = $("trace-case-id").value;
      const result = await api("POST", "/traces/" + encodeURIComponent(id) + "/case" + (save ? "?overwrite=1" : ""), body);
      $("trace-case-table").replaceChildren(el("table", {}, el("thead", {}, el("tr", {}, ["Path", "Operator", "Expected"].map((t) => el("th", { text: t })))),
        el("tbody", {}, ...result.case.assertions.map((a) => el("tr", {}, el("td", { class: "mono", text: a.path }), el("td", {}, pill(a.operator)), el("td", { class: "mono", text: short(a.expected) }))))));
      $("trace-case-notes").replaceChildren(...result.notes.map((n) => el("li", { class: "muted", text: n })));
      $("trace-case-out").textContent = pretty(result.case);
      out.hidden = false;
      if (save) toast("Saved case " + result.case.case_id, "ok");
    });
  }
  async function deleteTrace(id) {
    if (!window.confirm("Delete trace " + id + "?")) return;
    await load(async () => { await api("DELETE", "/traces/" + encodeURIComponent(id) + "?confirm=yes"); toast("Deleted " + id, "ok"); navigate("/traces"); });
  }

  // ---------------------------------------------------------------- command palette, shortcuts
  let paletteIndex = 0;
  let paletteRows = [];
  function paletteItems(query) {
    const nav = (path) => () => navigate(path);
    const items = [
      { label: "Go to Overview", hint: "g o", run: nav("/overview") }, { label: "Go to Cases", hint: "g c", run: nav("/cases") }, { label: "Go to Traces", hint: "g t", run: nav("/traces") },
      { label: "Switch theme", hint: THEMES[(theme + 1) % 3], run: () => setTheme(theme + 1) }, { label: "Keyboard shortcuts", hint: "?", run: () => openOverlay("help") },
      ...cache.cases.map((c) => ({ label: "Case: " + c.case_id, hint: c.name, run: nav("/cases/" + encodeURIComponent(c.case_id)) })),
      ...cache.traces.map((t) => ({ label: "Trace: " + t, hint: "", run: nav("/traces/" + encodeURIComponent(t)) })),
    ];
    const needle = query.trim().toLowerCase();
    return items.filter((i) => !needle || (i.label + " " + i.hint).toLowerCase().includes(needle)).slice(0, 30);
  }
  function drawPalette() {
    const list = $("palette-list");
    clear(list);
    paletteRows = paletteItems($("palette-input").value);
    paletteIndex = Math.min(paletteIndex, Math.max(paletteRows.length - 1, 0));
    paletteRows.forEach((item, index) => list.append(el("li", { role: "option", "aria-selected": String(index === paletteIndex), onclick: () => choosePalette(index) }, el("span", { text: item.label }), el("span", { class: "muted", text: item.hint }))));
    if (!paletteRows.length) list.append(el("li", { class: "muted", text: "Nothing matches." }));
  }
  function choosePalette(index) {
    const item = paletteRows[index];
    closeOverlays();
    if (item) item.run();
  }
  function openOverlay(name) {
    closeOverlays();
    $(name).hidden = false;
    if (name === "palette") { $("palette-input").value = ""; paletteIndex = 0; drawPalette(); $("palette-input").focus();
      load(async () => { const data = await api("GET", "/overview"); cache.cases = data.cases; if (!$("palette").hidden) drawPalette(); }); }
  }
  function closeOverlays() { $("palette").hidden = true; $("help").hidden = true; }
  const isTyping = (target) => target && (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable);
  let pendingG = false;
  function onKey(event) {
    if ($("shell").hidden) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); if ($("palette").hidden) openOverlay("palette"); else closeOverlays(); return; }
    if (event.key === "Escape") { closeOverlays(); return; }
    if (!$("palette").hidden && event.target === $("palette-input")) {
      if (event.key === "ArrowDown") { event.preventDefault(); paletteIndex = Math.min(paletteIndex + 1, paletteRows.length - 1); drawPalette(); }
      else if (event.key === "ArrowUp") { event.preventDefault(); paletteIndex = Math.max(paletteIndex - 1, 0); drawPalette(); }
      else if (event.key === "Enter") { event.preventDefault(); choosePalette(paletteIndex); }
      return;
    }
    if (isTyping(event.target) || event.ctrlKey || event.metaKey || event.altKey) return;
    if (pendingG) {
      pendingG = false;
      const target = { o: "/overview", c: "/cases", t: "/traces" }[event.key];
      if (target) { event.preventDefault(); navigate(target); }
      return;
    }
    if (event.key === "g") { pendingG = true; setTimeout(() => { pendingG = false; }, 1200); }
    else if (event.key === "?") { event.preventDefault(); openOverlay("help"); }
    else if (event.key === "/") { const f = $("filter"); if (f) { event.preventDefault(); f.focus(); } }
  }

  // ---------------------------------------------------------------- boot
  function init() {
    for (const holder of [$("brand-logo"), $("login-logo")]) holder.append(svg("svg", { viewBox: "0 0 24 24", fill: "none", stroke: "#fff", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round", width: "100%", height: "100%" }, svg("path", { d: ICONS.shield })));
    $("login-card").addEventListener("submit", (event) => {
      event.preventDefault();
      token = $("token-input").value.trim();
      $("token-input").value = "";
      if (token) showApp();
    });
    $("theme-toggle").addEventListener("click", () => setTheme(theme + 1));
    $("palette-open").addEventListener("click", () => openOverlay("palette"));
    $("palette-input").addEventListener("input", () => { paletteIndex = 0; drawPalette(); });
    $("palette").addEventListener("click", (event) => { if (event.target === $("palette")) closeOverlays(); });
    $("help").addEventListener("click", (event) => { if (event.target === $("help")) closeOverlays(); });
    $("help-close").addEventListener("click", closeOverlays);
    document.addEventListener("keydown", onKey);
    window.addEventListener("hashchange", route);
    const match = /(?:^|[#&])token=([^&]+)/.exec(window.location.hash);
    if (match) {
      token = decodeURIComponent(match[1]);
      window.history.replaceState(null, "", window.location.pathname + window.location.search + "#/overview");
      showApp();
    } else {
      showLogin("");
    }
  }

  init();
})();
