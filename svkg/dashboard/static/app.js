/* Student Visa Graph · Scenario lab. Vanilla JS, no dependencies. All text is inserted with textContent. */
(() => {
  "use strict";
  const app = document.getElementById("app");
  const state = { meta: null, scenarios: [], ranAll: null, ranAt: null, runs: {}, f: { q: "", rag: "all", group: "all", type: "all" } };

  // ---------- helpers ----------
  const SVG = "http://www.w3.org/2000/svg";
  function icon(name, cls) {
    const s = document.createElementNS(SVG, "svg");
    s.setAttribute("class", "i" + (cls ? " " + cls : ""));
    s.setAttribute("aria-hidden", "true");
    const u = document.createElementNS(SVG, "use");
    u.setAttribute("href", "#i-" + name);
    s.appendChild(u);
    return s;
  }
  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, v);
    }
    add(el, kids);
    return el;
  }
  function add(el, kids) {
    for (const k of kids) {
      if (k == null || k === false) continue;
      if (Array.isArray(k)) add(el, k);
      else el.appendChild(k instanceof Node ? k : document.createTextNode(String(k)));
    }
  }
  const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); };
  const live = (msg) => { document.getElementById("live").textContent = msg; };
  let toastTimer;
  function toast(msg) {
    const t = document.getElementById("toast");
    t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 2200);
  }
  async function api(path, method) {
    const r = await fetch(path, { method: method || "GET", cache: "no-store", headers: method === "POST" ? { "X-SVKG-Dashboard": "1" } : {} });
    let body = {};
    try { body = await r.json(); } catch (e) { /* not json */ }
    if (!r.ok) throw Object.assign(new Error(body.error || body.detail || r.statusText), { status: r.status });
    return body;
  }
  const RAGLABEL = { green: "Met", amber: "Needs a person", red: "Not met", grey: "Not applicable" };
  function ragInfo(result) {
    const m = state.meta && state.meta.rag && state.meta.rag[result];
    return m || { rag: "amber", icon: "help", label: result };
  }
  function chip(result) {
    const r = ragInfo(result);
    return h("span", { class: "chip rag-" + r.rag }, icon(r.icon), r.label);
  }
  function fmt(v) {
    if (v === true) return "yes";
    if (v === false) return "no";
    if (typeof v === "number") return v.toLocaleString("en-NZ");
    if (v == null) return "—";
    return String(v);
  }
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

  // ---------- theme and header ----------
  function setTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("svkg-theme", t); } catch (e) { /* private mode */ }
    const b = document.getElementById("theme");
    clear(b); b.appendChild(icon(t === "dark" ? "sun" : "moon"));
    b.setAttribute("aria-label", t === "dark" ? "Switch to light theme" : "Switch to dark theme");
  }
  document.getElementById("theme").addEventListener("click", () =>
    setTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark"));
  setTheme(document.documentElement.getAttribute("data-theme") || "light");

  function paintDb() {
    const pill = document.getElementById("db-pill"), text = document.getElementById("db-text"), m = state.meta;
    pill.classList.remove("ok", "bad");
    if (!m) return;
    if (m.connected) {
      pill.classList.add("ok");
      text.textContent = `${m.database} · ${(m.spec_nodes || 0).toLocaleString("en-NZ")} nodes`;
      pill.title = "Connected to the live Neo4j database";
    } else {
      pill.classList.add("bad");
      text.textContent = "Database offline";
      pill.title = m.error || "Neo4j is not reachable";
    }
  }

  // ---------- home ----------
  function expectedCounts() {
    const c = { green: 0, amber: 0, red: 0, grey: 0 };
    state.scenarios.forEach((s) => { c[s.expected_rag] += 1; });
    return c;
  }
  function kpi(n, label, cls, ic) {
    return h("div", { class: "kpi " + (cls || "") }, h("span", { class: "n" }, n), h("span", { class: "t" }, ic ? icon(ic) : null, label));
  }
  function renderHome() {
    document.title = "Scenarios · Student Visa Graph";
    const online = state.meta && state.meta.connected;
    const runAll = h("button", { class: "btn primary", type: "button", id: "run-all", disabled: !online,
      title: online ? "Run every scenario against the live graph (nothing is saved)" : "The database is offline" },
      icon("play"), "Run all scenarios");
    runAll.addEventListener("click", onRunAll);
    const ec = expectedCounts();
    const kp = h("div", { class: "kpis", id: "kpis" });
    if (state.ranAll) {
      const rows = Object.values(state.ranAll), met = rows.filter((r) => r.passed).length;
      kp.appendChild(kpi(`${met} / ${rows.length}`, "meet their expected result" + (state.ranAt ? ` · run ${state.ranAt}` : ""), met === rows.length ? "ok" : "bad", met === rows.length ? "check" : "x"));
      const rc = { green: 0, amber: 0, red: 0, grey: 0 };
      rows.forEach((r) => { rc[r.overall.rag] += 1; });
      kp.appendChild(kpi(rc.red, "ended red (a rule not met)", "rag-red", "x"));
      kp.appendChild(kpi(rc.amber, "ended amber (a person must act)", "rag-amber", "alert"));
      kp.appendChild(kpi(rc.green, "ended green (all clear)", "rag-green", "check"));
    } else {
      kp.appendChild(kpi(state.scenarios.length, "synthetic scenarios", "", "list"));
      kp.appendChild(kpi(ec.green, "expected to be met", "rag-green", "check"));
      kp.appendChild(kpi(ec.amber, "expected to need a person", "rag-amber", "user"));
      kp.appendChild(kpi(ec.red, "expected not to be met", "rag-red", "x"));
    }
    const groups = [...new Set(state.scenarios.map((s) => s.group))].sort();
    const types = [...new Set(state.scenarios.map((s) => s.test_type))].sort();
    const search = h("input", { type: "search", id: "q", placeholder: "Search scenarios, people, rules…", "aria-label": "Search scenarios", value: state.f.q });
    search.addEventListener("input", () => { state.f.q = search.value; paintGrid(); });
    const seg = h("div", { class: "seg", role: "group", "aria-label": "Filter by expected outcome" });
    [["all", "All"], ["green", "Met"], ["amber", "Needs a person"], ["red", "Not met"], ["grey", "N/A"]].forEach(([k, label]) => {
      const b = h("button", { type: "button", "aria-pressed": String(state.f.rag === k) }, k === "all" ? null : h("span", { class: "dot rag-" + k }), label);
      b.addEventListener("click", () => { state.f.rag = k; seg.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); paintGrid(); });
      seg.appendChild(b);
    });
    const sel = (id, label, opts, key) => {
      const s = h("select", { id, "aria-label": label }, h("option", { value: "all" }, label), opts.map((o) => h("option", { value: o }, o)));
      s.value = state.f[key];
      s.addEventListener("change", () => { state.f[key] = s.value; paintGrid(); });
      return s;
    };
    clear(app);
    app.append(
      h("div", { class: "hero" },
        h("div", {}, h("h1", {}, "Scenarios"),
          h("p", {}, "Synthetic applicants, each with an expected result. Open one to run it against the live rules and see every rule's result, explanation, source provision and the facts used.")),
        runAll),
      kp,
      h("div", { class: "toolbar" }, h("div", { class: "search" }, icon("search"), search), seg,
        sel("g", "All topics", groups, "group"), sel("t", "All test types", types, "type"),
        h("span", { class: "count", id: "count", "aria-live": "polite" })),
      h("div", { class: "grid", id: "grid" }));
    paintGrid();
  }
  function matches(s) {
    const f = state.f;
    if (f.rag !== "all" && s.expected_rag !== f.rag) return false;
    if (f.group !== "all" && s.group !== f.group) return false;
    if (f.type !== "all" && s.test_type !== f.type) return false;
    if (f.q) {
      const hay = [s.id, s.title, s.scenario, s.applicant, s.nationality, s.group, s.check_id, s.rule_id].join(" ").toLowerCase();
      return f.q.toLowerCase().split(/\s+/).every((w) => hay.includes(w));
    }
    return true;
  }
  function card(s) {
    const r = state.ranAll && state.ranAll[s.id];
    const foot = h("div", { class: "card-foot" }, h("span", {}, `${plural(s.facts, "fact")} · ${plural(s.evidence, "evidence item")}`),
      r ? h("span", { class: "verdict " + (r.passed ? "pass" : "fail") }, icon(r.passed ? "check" : "x"), r.passed ? "Matches expected" : "Differs from expected") : null);
    return h("a", { class: "card rag-" + s.expected_rag, href: "#/s/" + s.id, "aria-label": `${s.id}: ${s.title}. Expected: ${s.expected_label}` },
      h("div", { class: "card-top" }, h("span", { class: "tag mono" }, s.id), h("span", { class: "tag" }, s.group)),
      h("h3", {}, s.title),
      h("p", { class: "scn" }, s.scenario),
      h("div", { class: "exp-line" }, h("span", { class: "lbl" }, "Expected"), chip(s.expected_status)),
      r ? h("div", { class: "exp-line" }, h("span", { class: "lbl" }, "Actual"),
        h("span", { class: "chip rag-" + r.overall.rag, title: "Overall status: " + r.overall.status }, icon(BANNER_ICON[r.overall.rag]), RAGLABEL[r.overall.rag]),
        h("span", { class: "faint small" }, "worst of " + Object.values(r.rag_counts).reduce((a, b) => a + b, 0) + " rules")) : null,
      foot);
  }
  function paintGrid() {
    const grid = document.getElementById("grid");
    if (!grid) return;
    const list = state.scenarios.filter(matches);
    clear(grid);
    if (!list.length) grid.appendChild(h("div", { class: "empty" }, "No scenarios match these filters."));
    list.forEach((s) => grid.appendChild(card(s)));
    document.getElementById("count").textContent = `Showing ${list.length} of ${state.scenarios.length}`;
  }
  async function onRunAll(ev) {
    const b = ev.currentTarget;
    b.disabled = true; clear(b); b.append(h("span", { class: "spin" }), "Running 50 scenarios…");
    try {
      const res = await api("/api/run-all", "POST");
      state.ranAll = {}; res.results.forEach((r) => { state.ranAll[r.id] = r; });
      state.ranAt = new Date().toLocaleTimeString("en-NZ", { hour: "2-digit", minute: "2-digit" });
      live(`${res.passed} of ${res.total} scenarios met their expected result.`);
      renderHome();
      toast(`${res.passed} of ${res.total} met their expected result`);
    } catch (e) {
      b.disabled = false; clear(b); b.append(icon("play"), "Run all scenarios");
      toast("Could not run: " + e.message);
    }
  }

  // ---------- scenario page ----------
  const BANNER = {
    red: ["A requirement is not met", "One or more rules failed, or the engine stopped because a rule is ambiguous."],
    amber: ["Needs action by a person", "Nothing has failed, but evidence, a review or a decision is still outstanding."],
    green: ["All requirements met", "Every rule that applies is met."],
    grey: ["Nothing applies", "No rule applied to this case."],
  };
  const BANNER_ICON = { red: "x", amber: "alert", green: "check", grey: "minus" };

  function expectationList(items) {
    return h("ul", { class: "checklist" }, items.map((x) => {
      const cls = x.passed == null ? "" : x.passed ? "pass" : "fail";
      return h("li", { class: cls + (x.standing ? " std" : "") },
        h("span", { class: "mk" }, x.passed == null ? null : icon(x.passed ? "check" : "x")),
        h("span", {}, x.text, x.detail ? h("span", { class: "d" }, x.detail) : null));
    }));
  }
  function applicationPanel(d) {
    const a = d.applicant || {};
    const rows = [["Name", [a.given_name, a.family_name].filter(Boolean).join(" ")], ["Nationality", a.nationality], ["Lives in", [a.city_of_residence, a.country_of_residence].filter(Boolean).join(", ")],
      ["Provider", a.provider], ["Programme", a.programme], ["Lodged", d.channel], ["As at", d.assessment_as_at]].filter((r) => r[1]);
    const facts = h("table", { class: "facts" }, h("thead", {}, h("tr", {}, h("th", {}, "Fact"), h("th", {}, "Value"), h("th", {}, "Verified?"))),
      h("tbody", {}, d.facts.map((f) => h("tr", {}, h("td", {}, h("code", {}, f.attribute)), h("td", {}, fmt(f.value)),
        h("td", {}, h("span", { class: "vmk " + (f.verified ? "yes" : "no"), title: f.verified ? "Verified" : "Not verified" }, icon(f.verified ? "verified" : "unverified"), f.verified ? "Yes" : "No"))))));
    return h("section", { class: "panel", "aria-labelledby": "app-h" },
      h("h2", { id: "app-h" }, icon("file"), "The application"),
      h("dl", { class: "kv" }, rows.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
      h("details", { class: "fold", open: true }, h("summary", {}, icon("chev"), `Facts (${d.facts.length})`), facts),
      h("details", { class: "fold" }, h("summary", {}, icon("chev"), `Evidence (${d.evidence.length})`),
        h("ul", { class: "checklist" }, d.evidence.map((e) => h("li", { class: e.verified ? "pass" : "" },
          h("span", { class: "mk" }, icon(e.verified ? "check" : "minus")), h("span", {}, e.type, h("span", { class: "d" }, (e.reference || e.id) + (e.verified ? "" : " · not verified")))))))
      , d.synthetic_notice ? h("p", { class: "small faint", style: "margin-top:10px" }, d.synthetic_notice) : null);
  }

  async function renderScenario(id) {
    let d;
    try { d = await api("/api/scenarios/" + encodeURIComponent(id)); }
    catch (e) { clear(app); app.append(h("div", { class: "empty" }, "That scenario does not exist. ", h("a", { href: "#/" }, "Back to all scenarios"))); return; }
    document.title = `${d.id} · ${d.title}`;
    const idx = state.scenarios.findIndex((s) => s.id === id), prev = state.scenarios[idx - 1], next = state.scenarios[idx + 1];
    const online = state.meta && state.meta.connected;
    const save = h("input", { type: "checkbox", id: "save" });
    const runBtn = h("button", { class: "btn primary", type: "button", id: "run", disabled: !online }, icon("play"), "Run scenario");
    const results = h("div", { id: "results" });
    const expPanel = h("section", { class: "panel", "aria-labelledby": "exp-h" }, h("h2", { id: "exp-h" }, icon("check"), "Expected results"), h("div", { id: "exp" }, expectationList(d.expectations)));
    const bar = h("div", { class: "actionbar" }, runBtn,
      h("label", { class: "check", title: "Also record this assessment in the graph and the audit log" }, save, "Save assessment to the graph"),
      h("span", { class: "spacer" }),
      h("span", { class: "muted small", id: "ran-at" }, online ? "Not run yet. A run reads the rules from the live database and writes nothing unless you tick save." : "The database is offline, so scenarios can be read but not run."));
    clear(app);
    app.append(
      h("nav", { class: "crumbs", "aria-label": "Scenario navigation" },
        h("a", { href: "#/" }, icon("left"), "All scenarios"),
        h("span", {}, prev ? h("a", { href: "#/s/" + prev.id, "aria-label": "Previous scenario " + prev.id }, icon("left"), prev.id) : null, " ",
          next ? h("a", { href: "#/s/" + next.id, "aria-label": "Next scenario " + next.id }, next.id, icon("right")) : null)),
      h("header", { class: "s-head rag-" + d.expected_rag },
        h("div", { class: "s-meta" }, h("span", { class: "tag mono" }, d.id), h("span", { class: "tag" }, d.group), h("span", { class: "tag" }, d.test_type.toLowerCase().replace("_", " ")),
          h("span", { class: "tag" }, "synthetic persona")),
        h("h1", {}, d.title), h("p", { class: "scn" }, d.scenario),
        d.backstory ? h("p", { class: "back" }, d.backstory) : null,
        h("div", { class: "exp-line" }, h("span", { class: "lbl" }, "Expected"), chip(d.expected_status), d.expected_outcome ? h("span", { class: "muted small" }, d.expected_outcome) : null)),
      bar,
      h("div", { class: "layout" }, h("div", {}, results), h("aside", { class: "aside", "aria-label": "Expectations and application" }, expPanel, applicationPanel(d))));
    const cached = state.runs[id];
    if (cached) paintRun(d, cached, results, document.getElementById("exp"), document.getElementById("ran-at"));
    else results.appendChild(emptyRun());
    runBtn.addEventListener("click", async () => {
      runBtn.disabled = true; clear(runBtn); runBtn.append(h("span", { class: "spin" }), "Running…");
      try {
        const v = await api(`/api/scenarios/${encodeURIComponent(id)}/run?save=${save.checked ? 1 : 0}`, "POST");
        state.runs[id] = v;
        paintRun(d, v, results, document.getElementById("exp"), document.getElementById("ran-at"));
        live(`Run complete. ${BANNER[v.overall.rag][0]}. ${v.passed ? "Expected result met." : "Differs from expected."}`);
        results.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
      } catch (e) {
        clear(results);
        results.appendChild(h("div", { class: "alert", role: "alert" }, h("strong", {}, "The scenario could not be run"), e.message,
          h("p", { class: "small", style: "margin-top:6px" }, "Check that Neo4j is running and the studentvisa database is loaded (svkg load).")));
      } finally { runBtn.disabled = !online; clear(runBtn); runBtn.append(icon("play"), "Run again"); }
    });
  }
  function emptyRun() {
    const s = document.createElementNS(SVG, "svg"); s.setAttribute("class", "big"); s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("aria-hidden", "true");
    const u = document.createElementNS(SVG, "use"); u.setAttribute("href", "#i-play"); s.appendChild(u);
    return h("div", { class: "panel empty-run" }, s, h("h2", {}, "Ready to run"),
      h("p", {}, "Press Run scenario to assess this application against the live rules. You will see each rule's result, why, the provision it comes from, and the facts it used."));
  }

  function paintRun(d, v, results, expEl, ranAt) {
    clear(results); clear(expEl);
    expEl.appendChild(expectationList(v.expectations));
    ranAt.textContent = `${v.saved ? "Run and saved" : "Dry run, nothing saved"} · ${new Date().toLocaleTimeString("en-NZ")} · ${v.assessment.assessment_id}`;
    const rag = v.overall.rag, [title, sub] = BANNER[rag], c = v.rag_counts, total = c.red + c.amber + c.green + c.grey;
    const seg = (k, cls) => c[k] ? h("span", { class: cls, style: `flex:${c[k]}`, title: `${c[k]} ${RAGLABEL[k].toLowerCase()}` }) : null;
    const copy = h("button", { class: "btn", type: "button" }, icon("copy"), "Copy summary for Claude");
    copy.addEventListener("click", () => copyForClaude(v));
    const dl = h("button", { class: "btn", type: "button" }, icon("download"), "Download JSON");
    dl.addEventListener("click", () => download(v));
    results.append(
      h("section", { "aria-label": "Overall result" },
        h("div", { class: "banner rag-" + rag, role: "group" },
          h("span", { class: "badge" }, icon(BANNER_ICON[rag])),
          h("div", {}, h("h2", {}, title), h("p", {}, sub), h("p", { class: "mono small" }, v.overall.status)),
          h("div", { class: "met" }, h("span", { class: "chip " + (v.passed ? "rag-green" : "rag-red") }, icon(v.passed ? "check" : "x"), v.passed ? "Expected result met" : "Differs from expected"),
            h("span", { class: "small" }, `${v.expectations.filter((x) => x.passed).length} of ${v.expectations.length} expectations passed`))),
        h("div", { class: "panel", style: "margin-top:14px" },
          h("div", { style: "display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:center" },
            h("strong", {}, `${plural(total, "rule")} checked`), h("span", { style: "display:flex;gap:8px;flex-wrap:wrap" }, copy, dl)),
          h("div", { class: "bar", role: "img", "aria-label": `${c.green} met, ${c.amber} need a person, ${c.red} not met, ${c.grey} not applicable` }, seg("red", "r"), seg("amber", "a"), seg("green", "g"), seg("grey", "n")),
          h("div", { class: "legend" },
            [["red", "x", "Not met"], ["amber", "alert", "Needs a person"], ["green", "check", "Met"], ["grey", "minus", "Not applicable"]].map(([k, ic, l]) =>
              h("span", { class: "rag-" + k }, h("span", { class: "dot" }), icon(ic), `${c[k]} ${l}`))))),
      actionsPanel(v),
      resultsSection(v));
  }
  function actionsPanel(v) {
    const acts = v.digest.key_actions, CAP = 8;
    const item = (a) => h("li", { class: "rag-" + a.rag },
      h("span", { class: "num", "aria-hidden": "true" }, a.order),
      h("div", {}, h("div", { class: "who" }, h("span", { class: "chip rag-" + a.rag }, a.owner),
        a.items ? h("span", { class: "tag" }, plural(a.items.length, "rule")) : h("span", { class: "tag mono" }, a.rule_id), a.provision && !a.items ? h("span", { class: "tag" }, a.provision) : null),
        h("p", { class: "txt" }, a.action)));
    const list = h("ol", { class: "actions" }, acts.slice(0, CAP).map(item));
    const panel = h("section", { class: "panel", "aria-labelledby": "act-h", style: "margin-top:16px" },
      h("h2", { id: "act-h" }, icon("list"), `Key actions (${acts.length})`), list);
    if (acts.length > CAP) {
      const more = h("button", { class: "btn small", type: "button", style: "margin-top:10px" }, `Show all ${acts.length}`);
      more.addEventListener("click", () => { acts.slice(CAP).forEach((a) => list.appendChild(item(a))); more.remove(); });
      panel.appendChild(more);
    }
    return panel;
  }
  function resultsSection(v) {
    let filter = "all";
    const wrap = h("section", { "aria-labelledby": "rules-h" });
    const list = h("div", {});
    const seg = h("div", { class: "seg", role: "group", "aria-label": "Filter rules by status" });
    const c = v.rag_counts;
    [["all", "All"], ["red", `Not met ${c.red}`], ["amber", `Needs a person ${c.amber}`], ["green", `Met ${c.green}`], ["grey", `N/A ${c.grey}`]].forEach(([k, label]) => {
      const b = h("button", { type: "button", "aria-pressed": String(k === "all") }, k === "all" ? null : h("span", { class: "dot rag-" + k }), label);
      b.addEventListener("click", () => { filter = k; seg.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); paint(); });
      seg.appendChild(b);
    });
    const exp = h("button", { class: "btn small", type: "button" }, "Expand all"), col = h("button", { class: "btn small", type: "button" }, "Collapse all");
    exp.addEventListener("click", () => list.querySelectorAll("details.rule").forEach((x) => { x.open = true; }));
    col.addEventListener("click", () => list.querySelectorAll("details.rule").forEach((x) => { x.open = false; }));
    function paint() {
      clear(list);
      v.groups.forEach((g) => {
        const evs = g.evaluations.filter((e) => filter === "all" || e.rag === filter);
        if (!evs.length) return;
        list.appendChild(h("section", { class: "group rag-" + g.rag, "aria-label": g.label },
          h("div", { class: "group-h" }, h("span", { class: "dot" }), h("h3", {}, g.label), h("span", { class: "tag mono" }, g.check_id),
            h("span", { class: "cnt" }, ["red", "amber", "green", "grey"].filter((k) => g.counts[k]).map((k) => h("span", { class: "chip rag-" + k }, `${g.counts[k]} ${RAGLABEL[k].toLowerCase()}`)))),
          h("div", { class: "rules" }, evs.map((e) => ruleCard(e, e.rag === "red")))));
      });
      if (!list.firstChild) list.appendChild(h("div", { class: "empty" }, "No rules with this status."));
    }
    wrap.append(h("div", { class: "filters" }, h("h2", { id: "rules-h" }, "Rule by rule"), h("div", { style: "display:flex;gap:8px;flex-wrap:wrap;align-items:center" }, seg, exp, col)), list);
    paint();
    return wrap;
  }
  const trunc = (t, n) => { t = (t || "").replace(/\s+/g, " ").trim(); return t.length > n ? t.slice(0, n - 1).trimEnd() + "…" : t; };

  function ruleCard(e, open) {
    const stmt = e.rulespeak_statement || "";
    const sec = (title, ...kids) => h("div", { class: "sec" }, h("h4", {}, title), kids);
    const body = h("div", { class: "rule-body" });
    body.append(sec("Rule", h("p", { class: "why" }, stmt || "—"),
      h("div", { class: "chips", style: "margin-top:6px" }, h("span", { class: "tag" }, e.catalogue || "—"), h("span", { class: "tag" }, e.provenance_status || "—"),
        e.label ? h("span", { class: "tag" }, e.label) : null, e.exception_rule_id ? h("span", { class: "tag mono" }, "exemption " + e.exception_rule_id) : null)),
      sec("Why this result", h("p", { class: "why" }, e.explanation), (e.notes || []).map((n) => h("p", { class: "note" }, n))));
    if (e.calculation) {
      const k = e.calculation;
      body.append(sec("Calculation", h("dl", { class: "kv" }, Object.entries(k).map(([a, b]) => [h("dt", {}, a.replace(/_/g, " ")), h("dd", {}, fmt(b))]))));
    }
    // source provision
    const text = e.provision_text, long = text && text.length > 420;
    const quote = text ? h("blockquote", { class: "quote" }, text) : null;
    const more = long ? h("button", { class: "btn small", type: "button", style: "margin-top:6px" }, "Show full text") : null;
    if (more) { quote.style.maxHeight = "9.5em"; more.addEventListener("click", () => { const o = quote.classList.toggle("open"); quote.style.maxHeight = o ? "none" : "9.5em"; more.textContent = o ? "Show less" : "Show full text"; }); }
    body.append(sec("Source provision", h("div", { class: "source" },
      h("div", { class: "src-top" }, h("strong", {}, e.provision || "No provision"), h("span", { class: "muted" }, e.source_title || e.regulatory_instrument || ""),
        e.source_version ? h("span", { class: "tag" }, "version " + e.source_version) : null,
        e.provision_effective_from ? h("span", { class: "tag" }, "in force from " + e.provision_effective_from) : null,
        e.provision_url ? h("a", { href: e.provision_url, target: "_blank", rel: "noopener noreferrer", "aria-label": "Open the source page (new tab)" }, "Source ", icon("ext")) : null),
      quote || h("p", { class: "note", style: "margin-top:6px" }, e.source_text_status === "SOURCE_TEXT_NOT_RETRIEVED" ? "The exact wording has not been retrieved for this provision." : "No provision text is attached to this rule."),
      more)));
    if (e.facts_used && e.facts_used.length)
      body.append(sec("Facts used", h("div", { class: "chips" }, e.facts_used.map((f) => {
        const ver = f.verification_status === "VERIFIED";
        return h("span", { class: "fact" + (ver ? "" : " unv"), title: ver ? "Verified fact" : "Observed, not verified" },
          h("span", { class: "vmk " + (ver ? "yes" : "no") }, icon(ver ? "verified" : "unverified")), h("span", { class: "k" }, f.attribute), h("span", { class: "v" }, fmt(f.value)),
          f.evidence && f.evidence.length ? h("span", { class: "ev" }, "· " + [...new Set(f.evidence.map((x) => x.type))].join(", ")) : null);
      }))));
    else body.append(sec("Facts used", h("p", { class: "note" }, "No application facts were needed for this result.")));
    if (e.evidence_used && e.evidence_used.length)
      body.append(sec("Evidence used", h("div", { class: "chips" }, e.evidence_used.map((x) => h("span", { class: "fact" + (x.verified ? "" : " unv") },
        h("span", { class: "vmk " + (x.verified ? "yes" : "no") }, icon(x.verified ? "verified" : "unverified")), h("span", { class: "v" }, x.type))))));
    if (e.unresolved_requirements && e.unresolved_requirements.length)
      body.append(sec("Still needed", h("ul", { class: "need" }, e.unresolved_requirements.map((u) => h("li", {}, u)))));
    const pres = e.instruction_rules_presented || e.rules_presented || [];
    if (pres.length) body.append(sec(`Rules presented for review (${pres.length})`, h("div", { class: "chips" }, pres.map((r) => h("span", { class: "tag mono" }, r)))));
    return h("details", { class: "rule rag-" + e.rag, open: open },
      h("summary", {}, h("div", { class: "rule-top" }, chip(e.result), h("span", { class: "tag mono" }, e.rule_id), e.provision ? h("span", { class: "tag" }, e.provision) : null,
        e.automation_class ? h("span", { class: "tag cls", title: "Automation class" }, e.automation_class) : null, h("span", { class: "chev" }, icon("chev"))),
        stmt ? h("div", { class: "rule-stmt" }, trunc(stmt, 190)) : null, h("div", { class: "rule-why" }, e.explanation)),
      body);
  }

  async function copyForClaude(v) {
    const text = "Summarise this Student Visa scenario result for an immigration officer, with key actions. Follow the writing_guidance field in the data. Do not recommend granting or declining.\n\n```json\n" + JSON.stringify(v.digest, null, 1) + "\n```";
    try { await navigator.clipboard.writeText(text); toast("Copied. Paste it into Claude Desktop."); }
    catch (e) {
      const t = h("textarea", { style: "position:fixed;opacity:0" }); t.value = text; document.body.appendChild(t); t.select();
      try { document.execCommand("copy"); toast("Copied. Paste it into Claude Desktop."); } catch (err) { toast("Copy failed. Use Download JSON."); }
      t.remove();
    }
  }
  function download(v) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(v, null, 2)], { type: "application/json" }));
    const a = h("a", { href: url, download: `${v.scenario.id}-result.json` }); document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  }

  // ---------- router ----------
  async function route() {
    const m = location.hash.match(/^#\/s\/([\w.-]+)/);
    if (m) await renderScenario(m[1]); else renderHome();
    window.scrollTo(0, 0);
    app.focus({ preventScroll: true });
  }
  async function init() {
    try {
      [state.meta, state.scenarios] = await Promise.all([api("/api/meta"), api("/api/scenarios")]);
      paintDb();
    } catch (e) {
      clear(app); app.append(h("div", { class: "alert", role: "alert" }, h("strong", {}, "The dashboard could not load"), e.message));
      return;
    }
    window.addEventListener("hashchange", route);
    route();
  }
  init();
})();
