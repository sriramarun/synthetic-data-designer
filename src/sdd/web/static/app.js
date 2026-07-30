/* Front end for the synthetic data designer.
 *
 * One rule shapes the whole file: the browser holds the spec as a plain object
 * and every edit posts it back for validation. There is no client-side copy of
 * the rules — the loader that validates a spec here is the same one the CLI
 * uses, so the UI cannot accept something the engine would reject.
 */

const VIEWS = ["source", "profile", "configure", "results"];

const state = {
  view: "source",
  spec: null,        // the spec being edited, as a plain object
  profile: null,     // the analysis, when the spec came from a file
  valid: false,
  problems: [],
  job: null,
  result: null,
  reachable: { source: true, profile: false, configure: false, results: false },
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const el = (tag, attrs = {}, kids = []) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const kid of [].concat(kids)) {
    if (kid) node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return node;
};

const fmt = {
  int: (n) => (n === null || n === undefined ? "—" : Number(n).toLocaleString()),
  pct: (n, dp = 1) => (n === null || n === undefined ? "—" : `${(n * 100).toFixed(dp)}%`),
  num: (n, dp = 4) => (n === null || n === undefined ? "—" : Number(n).toFixed(dp)),
  bytes: (n) => {
    if (n < 1024) return `${n} B`;
    if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
    if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
    return `${(n / 1024 ** 3).toFixed(2)} GB`;
  },
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  let payload;
  try { payload = text ? JSON.parse(text) : {}; } catch { payload = { detail: text }; }
  if (!response.ok) throw new Error(payload.detail || payload.error || `HTTP ${response.status}`);
  return payload;
}

/* ------------------------------------------------------------- navigation */

function show(view) {
  state.view = view;
  VIEWS.forEach((name) => { $(`#view-${name}`).hidden = name !== view; });
  $$(".step").forEach((button) => {
    const target = button.dataset.view;
    button.setAttribute("aria-current", String(target === view));
    button.disabled = !state.reachable[target];
    button.classList.toggle("complete", state.reachable[target] && VIEWS.indexOf(target) < VIEWS.indexOf(view));
  });
  $("#btn-back").hidden = view === "source";
  const next = $("#btn-next");
  if (view === "configure") {
    next.textContent = "Generate";
    next.disabled = !state.valid;
  } else if (view === "results") {
    next.textContent = "Run again";
    next.disabled = false;
  } else {
    next.textContent = "Next";
    next.disabled = !state.spec;
  }
  window.scrollTo({ top: 0, behavior: "instant" });
}

function reach(view) { state.reachable[view] = true; }
function status(message, kind = "") {
  const node = $("#footer-status");
  node.innerHTML = message;
  node.style.color = kind === "bad" ? "var(--bad)" : kind === "good" ? "var(--good)" : "";
}

/* ------------------------------------------------------------ spec loading */

async function adoptSpec(spec, { profile = null, origin = "" } = {}) {
  state.spec = spec;
  state.profile = profile;
  $("#spec-name").textContent = origin;
  await validate();
  reach("configure");
  if (profile) reach("profile");
  renderConfigure();
}

async function validate() {
  try {
    const result = await api("/api/check", { method: "POST", body: JSON.stringify(state.spec) });
    state.valid = result.valid;
    state.problems = result.problems || [];
    state.summary = result.spec;
  } catch (error) {
    state.valid = false;
    state.problems = [error.message];
  }
  if (state.view === "configure") $("#btn-next").disabled = !state.valid;
  if (state.valid) {
    status(`Spec is valid — ${state.summary.columns} columns, ${state.summary.periods} periods.`, "good");
  } else {
    status(`Spec has ${state.problems.length} problem(s): ${state.problems[0] || ""}`, "bad");
  }
  return state.valid;
}

/* ---------------------------------------------------------------- 1. source */

async function loadPacks() {
  const meta = await api("/api/meta");
  $("#version").textContent = `v${meta.version}`;
  const list = $("#packs");
  list.replaceChildren();
  if (!meta.packs.length) {
    list.append(el("p", { class: "hint", text: "No packs bundled." }));
    return;
  }
  for (const name of meta.packs) {
    const info = await api(`/api/packs/${name}`);
    list.append(
      el("button", { class: "pack", onclick: () => choosePack(name, info) }, [
        el("span", {}, [
          el("div", { class: "name", text: name }),
          el("div", { class: "m", text: `${info.summary.asset_class} · ${info.summary.columns} columns · ${info.summary.periods} periods · ${info.summary.scenarios.join(", ") || "no scenarios"}` }),
        ]),
        el("span", { class: "spacer" }),
        el("span", { class: "badge", text: "Load" }),
      ])
    );
  }
}

async function choosePack(name, info) {
  state.reachable.profile = false;
  await adoptSpec(structuredClone(info.spec), { origin: `pack: ${name}` });
  show("configure");
}

function wireUpload() {
  const drop = $("#drop");
  const input = $("#file");
  drop.addEventListener("click", () => input.click());
  drop.addEventListener("dragover", (event) => { event.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    drop.classList.remove("over");
    if (event.dataTransfer.files[0]) handleUpload(event.dataTransfer.files[0]);
  });
  input.addEventListener("change", () => { if (input.files[0]) handleUpload(input.files[0]); });
}

async function handleUpload(file) {
  const target = $("#upload-status");
  target.replaceChildren(el("div", { class: "note", html: `<span class="spin"></span> Uploading ${file.name} (${fmt.bytes(file.size)})…` }));
  try {
    const form = new FormData();
    form.append("file", file);
    const uploaded = await api("/api/upload", { method: "POST", body: form });

    target.replaceChildren(el("div", { class: "note", html: `<span class="spin"></span> Analysing ${uploaded.columns.length} columns…` }));
    const designed = await api("/api/design", {
      method: "POST",
      body: JSON.stringify({ file: uploaded.file, name: file.name.replace(/\.[^.]+$/, "").replace(/\W+/g, "_").toLowerCase() }),
    });

    target.replaceChildren(el("div", { class: "note good", text: `Analysed ${uploaded.original_name}.` }));
    await adoptSpec(designed.spec, { profile: designed.profile, origin: `from: ${uploaded.original_name}` });
    renderProfile();
    show("profile");
  } catch (error) {
    target.replaceChildren(el("div", { class: "note bad", text: error.message }));
  }
}

/* --------------------------------------------------------------- 2. profile */

function renderProfile() {
  const p = state.profile;
  if (!p) return;

  $("#profile-hint").textContent = p.is_panel
    ? "The file holds the same entities across several cut-offs, so behaviour over time could be measured as well as the shape of each column."
    : "The file is a single snapshot. Column shapes were measured, but with nothing observed twice there is no way to tell a fixed field from a moving one, or to learn any dynamics.";

  const roles = {};
  for (const column of p.columns) roles[column.role] = (roles[column.role] || 0) + 1;

  $("#profile-stats").replaceChildren(
    stat("Rows", fmt.int(p.rows)),
    stat("Entities", fmt.int(p.entities)),
    stat("Cut-offs", fmt.int(p.periods)),
    stat("Columns", fmt.int(p.columns.length)),
    stat("Need review", fmt.int(p.needs_review.length)),
  );

  const detection = $("#profile-detection");
  detection.replaceChildren();
  detection.append(el("div", { class: "note", html:
    `<strong>Entity</strong> <code class="inline">${p.id_column || "not found"}</code> — ${p.detection_notes.id_column || ""}` }));
  detection.append(el("div", { class: "note", html:
    `<strong>Cut-off</strong> <code class="inline">${p.time_column || "not found"}</code> — ${p.detection_notes.time_column || ""}` }));
  detection.append(el("div", { class: "hint", text:
    `Roles: ${Object.entries(roles).map(([k, v]) => `${v} ${k}`).join(", ")}. A static column never changes for an entity; a dynamic one does; a constant is the same for the whole pool.` }));

  // -- learned dynamics
  const card = $("#profile-dynamics-card");
  const dyn = p.dynamics || {};
  card.hidden = !Object.keys(dyn).length;
  const body = $("#profile-dynamics");
  body.replaceChildren();

  if (dyn.lifecycle) {
    const lc = dyn.lifecycle;
    body.append(el("div", { class: "note", html:
      `<strong>Lifecycle</strong> — ${lc.states.length} states in <code class="inline">${lc.state_column}</code>, ` +
      `${fmt.int(lc.observed_transitions)} transitions counted. ` +
      `Terminal: ${lc.terminal.map((s) => `<code class="inline">${s}</code>`).join(", ") || "none"}. ` +
      `Absorbing: ${lc.absorbing.map((s) => `<code class="inline">${s}</code>`).join(", ") || "none"}.` }));
    if (lc.low_evidence_states?.length) {
      body.append(el("div", { class: "note warn", text:
        `Thin evidence for ${lc.low_evidence_states.join(", ")} — those matrix rows rest on very few observations and are worth checking.` }));
    }
  }
  if (dyn.attrition) {
    body.append(el("div", { class: "note", html:
      `<strong>Attrition</strong> — ${fmt.pct(dyn.attrition.period_rate, 2)} per period, ${fmt.pct(dyn.attrition.annual_rate, 1)} annualised, over ${dyn.attrition.periods_observed} cut-offs.` }));
  }
  if (dyn.amortisation) {
    body.append(el("div", { class: "note", html:
      `<strong>Amortisation</strong> — <code class="inline">${dyn.amortisation.kind}</code> on <code class="inline">${dyn.amortisation.balance}</code>. ${dyn.amortisation.reason}.` }));
  }
  if (dyn.counters?.length) {
    body.append(el("div", { class: "note", html:
      `<strong>Counters</strong> — ${dyn.counters.map((c) => `<code class="inline">${c.column}</code> ${c.step > 0 ? "+" : ""}${c.step}`).join(", ")}.` }));
  }
  if (dyn.indices?.length) {
    body.append(el("div", { class: "note", html:
      `<strong>Index drift</strong> — ${dyn.indices.map((i) => `${i.applies_to.join(", ")} at ${fmt.pct(i.annual, 2)}/yr`).join("; ")}.` }));
  }
  if (p.derived?.length) {
    body.append(el("div", { class: "note good", html:
      `<strong>Derived columns</strong> — ${p.derived.map((d) => `<code class="inline">${d.target}</code> is a binning of <code class="inline">${d.source}</code>`).join(", ")}. These are recomputed rather than sampled, so they can never disagree with their source.` }));
  }

  renderProfileColumns();
  $("#profile-filter").oninput = renderProfileColumns;
  $("#profile-review-only").onchange = renderProfileColumns;
}

function renderProfileColumns() {
  const p = state.profile;
  const needle = $("#profile-filter").value.trim().toLowerCase();
  const reviewOnly = $("#profile-review-only").checked;
  const rows = p.columns.filter((c) =>
    (!needle || c.name.toLowerCase().includes(needle)) &&
    (!reviewOnly || c.confidence < 0.5)
  );

  const table = $("#profile-columns");
  table.replaceChildren(
    el("thead", {}, el("tr", {}, [
      el("th", { text: "Column" }), el("th", { text: "Type" }), el("th", { text: "Role" }),
      el("th", { text: "Fitted as" }), el("th", { class: "num", text: "Distinct" }),
      el("th", { class: "num", text: "Null" }), el("th", { class: "num", text: "Confidence" }),
    ])),
    el("tbody", {}, rows.map((c) => {
      const badge = c.confidence >= 0.75 ? "good" : c.confidence >= 0.5 ? "warn" : "bad";
      return el("tr", {}, [
        el("td", {}, el("code", { class: "inline", text: c.name })),
        el("td", { text: c.dtype }),
        el("td", {}, el("span", { class: "badge role", text: c.role })),
        el("td", { text: c.fit ? c.fit.method : "—" }),
        el("td", { class: "num", text: fmt.int(c.distinct) }),
        el("td", { class: "num", text: fmt.pct(c.nulls, 1) }),
        el("td", { class: "num" }, el("span", { class: `badge ${badge}`, text: fmt.num(c.confidence, 2) })),
      ]);
    }))
  );
}

function stat(key, value, small = false) {
  return el("div", { class: "stat" }, [
    el("div", { class: "k", text: key }),
    el("div", { class: `v${small ? " sm" : ""}`, text: value }),
  ]);
}

/* ------------------------------------------------------------- 3. configure */

function renderConfigure() {
  const spec = state.spec;
  if (!spec) return;

  $("#cfg-periods").value = spec.entity.calendar.periods;
  $("#cfg-periods-sub").textContent = `${spec.entity.calendar.freq.replace("_", " ")} from ${spec.entity.calendar.start}.`;

  const scenarios = Object.keys(spec.scenarios || {});
  const select = $("#cfg-scenario");
  select.replaceChildren(el("option", { value: "", text: scenarios.length ? "None (base calibration)" : "No scenarios in this spec" }));
  for (const name of scenarios) {
    select.append(el("option", { value: name, text: name }));
  }
  select.disabled = !scenarios.length;
  select.onchange = () => {
    const chosen = spec.scenarios?.[select.value];
    $("#cfg-scenario-sub").textContent = chosen?.description
      || "A stress overlay that shifts defaults, prepayment and collateral together.";
  };

  renderDynamics();
  renderLifecycle();
  renderColumns();
  refreshYaml();
}

function renderDynamics() {
  const spec = state.spec;
  const host = $("#cfg-dynamics");
  host.replaceChildren();
  const dyn = spec.dynamics || {};

  if (dyn.amortisation) {
    const am = dyn.amortisation;
    const kinds = ["annuity", "linear", "bullet", "interest_only", "revolving", "depreciation", "none"];
    const grid = el("div", { class: "grid three" });
    grid.append(labelled("Amortisation", "How the balance moves each period.",
      el("select", { onchange: (e) => { am.kind = e.target.value; validate(); refreshYaml(); } },
        kinds.map((k) => el("option", { value: k, text: k, selected: k === am.kind })))));
    grid.append(labelled("Balance column", "The column being paid down.",
      el("input", { type: "text", value: am.balance, disabled: true })));
    if (am.only_when_state) {
      grid.append(labelled("Only when", "States in which a payment is assumed. Everything else freezes the balance.",
        el("input", { type: "text", value: [].concat(am.only_when_state).join(", "), disabled: true })));
    }
    if (am.rate_per_period !== undefined && am.rate_per_period !== null) {
      grid.append(labelled("Rate per period", "Proportional change each period.",
        slider(am.rate_per_period, 0, 0.25, 0.005, (v) => { am.rate_per_period = v; validate(); refreshYaml(); }, (v) => fmt.pct(v, 1))));
    }
    host.append(grid);
  } else {
    host.append(el("p", { class: "hint", text: "This spec has no amortisation rule, so balances do not move on their own." }));
  }

  for (const index of dyn.indices || []) {
    if (index.kind !== "constant_drift") continue;
    host.append(el("div", { style: "margin-top:16px" },
      labelled(`Index: ${index.name}`,
        `Applied to ${index.applies_to.join(", ")}. Negative means falling collateral.`,
        slider(index.annual, -0.30, 0.20, 0.005, (v) => { index.annual = v; validate(); refreshYaml(); }, (v) => `${(v * 100).toFixed(1)}%/yr`))));
  }

  if ((dyn.counters || []).length) {
    host.append(el("p", { class: "hint", style: "margin-top:16px", html:
      `Counters ticking each period: ${dyn.counters.map((c) => `<code class="inline">${c.column} ${c.step > 0 ? "+" : ""}${c.step ?? c.expr}</code>`).join(" ")}` }));
  }
}

function renderLifecycle() {
  const spec = state.spec;
  const lc = spec.lifecycle;
  $("#card-lifecycle").hidden = !lc;
  if (!lc) return;

  const states = lc.transition_states || lc.states.filter((s) => !(lc.terminal || []).includes(s));
  const host = $("#cfg-matrix");

  const head = el("tr", {}, [el("th", { class: "rowhdr", text: "from ╲ to" })]);
  for (const name of states) head.append(el("th", { class: "num", text: name }));
  head.append(el("th", { class: "num", text: "Σ" }));

  const body = el("tbody");
  (lc.transitions || []).forEach((row, i) => {
    const tr = el("tr", {}, [el("th", { class: "rowhdr", text: states[i] })]);
    row.forEach((value, j) => {
      const input = el("input", {
        type: "number", step: "0.0001", min: "0", max: "1", value: value,
        oninput: (event) => {
          const parsed = parseFloat(event.target.value);
          lc.transitions[i][j] = Number.isFinite(parsed) ? parsed : 0;
          updateRowSum(i);
        },
        onchange: () => { validate(); refreshYaml(); },
      });
      tr.append(el("td", { class: i === j ? "diag" : "" }, input));
    });
    tr.append(el("td", { class: "sum", "data-sum": i }));
    body.append(tr);
  });

  const table = el("table", {}, [el("thead", {}, head), body]);
  host.replaceChildren(table);
  (lc.transitions || []).forEach((_, i) => updateRowSum(i));

  // -- hazards
  const hazards = $("#cfg-hazards");
  hazards.replaceChildren();
  if (!(lc.hazards || []).length) return;
  hazards.append(el("h2", { style: "font-size:13px;margin:0 0 3px", text: "Hazards" }));
  hazards.append(el("p", { class: "hint", text: "Events decided outside the matrix. A Bernoulli hazard fires on a flat per-period chance; a dwell-time hazard fires after a fixed number of periods in one state." }));
  const grid = el("div", { class: "grid two" });
  for (const hz of lc.hazards) {
    if (hz.kind === "bernoulli" && hz.annual_rate !== undefined) {
      grid.append(labelled(`${hz.name} → ${hz.to_state}`, "Annualised probability.",
        slider(hz.annual_rate, 0, 0.5, 0.005, (v) => { hz.annual_rate = v; validate(); refreshYaml(); }, (v) => `${(v * 100).toFixed(1)}%/yr`)));
    } else if (hz.kind === "dwell_time") {
      grid.append(labelled(`${hz.name}: ${hz.from_state} → ${hz.to_state}`, "Periods in the state before it fires.",
        el("input", { type: "number", min: "1", step: "1", value: hz.periods,
          onchange: (e) => { hz.periods = Math.max(1, parseInt(e.target.value, 10) || 1); validate(); refreshYaml(); } })));
    }
  }
  hazards.append(grid);
}

function updateRowSum(i) {
  const lc = state.spec.lifecycle;
  const total = (lc.transitions[i] || []).reduce((a, b) => a + (Number(b) || 0), 0);
  const cell = $(`#cfg-matrix td.sum[data-sum="${i}"]`);
  if (!cell) return;
  const ok = Math.abs(total - 1) < 1e-6;
  cell.textContent = total.toFixed(4);
  cell.className = `sum ${ok ? "ok" : "off"}`;
}

function renderColumns() {
  const spec = state.spec;
  const host = $("#cfg-columns");
  const needle = $("#cfg-filter").value.trim().toLowerCase();
  host.replaceChildren();

  const editable = spec.columns.filter((c) => c.generator && (!needle || c.name.toLowerCase().includes(needle)));
  if (!editable.length) {
    host.append(el("p", { class: "hint", text: "No sampled columns match." }));
    return;
  }

  for (const column of editable.slice(0, 120)) {
    const gen = column.generator;
    const row = el("div", { class: "col-row" });
    const head = el("div", { class: "col-head" }, [
      el("code", { class: "cname", text: column.name }),
      el("span", { class: "badge role", text: column.role }),
      el("span", { class: "badge", text: gen.kind }),
      el("span", { class: "spacer" }),
      column.confidence !== undefined && column.confidence !== null
        ? el("span", { class: `badge ${column.confidence >= 0.75 ? "good" : column.confidence >= 0.5 ? "warn" : "bad"}`, text: `conf ${fmt.num(column.confidence, 2)}` })
        : null,
    ]);
    row.append(head);

    const params = el("div", { class: "col-params" });
    if (gen.kind === "categorical" && gen.weights) {
      gen.values.forEach((value, i) => {
        params.append(labelled(String(value), null,
          el("input", {
            type: "number", step: "0.001", min: "0", value: gen.weights[i],
            onchange: (event) => {
              const parsed = parseFloat(event.target.value);
              gen.weights[i] = Number.isFinite(parsed) ? parsed : 0;
              validate(); refreshYaml();
            },
          })));
      });
    } else if (gen.kind === "scipy") {
      for (const [key, value] of Object.entries(gen.params || {})) {
        params.append(labelled(key, null,
          el("input", { type: "number", step: "any", value: value,
            onchange: (event) => { gen.params[key] = parseFloat(event.target.value); validate(); refreshYaml(); } })));
      }
    } else if (gen.kind === "gaussian") {
      params.append(labelled("mean", null, numberInput(gen, "mean")));
      params.append(labelled("stddev", null, numberInput(gen, "stddev")));
    } else if (gen.kind === "uniform") {
      params.append(labelled("low", null, numberInput(gen, "low")));
      params.append(labelled("high", null, numberInput(gen, "high")));
    } else if (gen.kind === "bernoulli") {
      params.append(labelled("p", "Probability of the true value.",
        slider(gen.p, 0, 1, 0.005, (v) => { gen.p = v; validate(); refreshYaml(); }, (v) => v.toFixed(3))));
    } else if (gen.kind === "constant") {
      params.append(labelled("value", null,
        el("input", { type: "text", value: gen.value ?? "",
          onchange: (event) => { gen.value = event.target.value; validate(); refreshYaml(); } })));
    } else {
      params.append(el("p", { class: "hint", style: "margin:0", text:
        `${gen.kind} — edit this one in the raw YAML below.` }));
    }
    row.append(params);

    if (column.review) row.append(el("div", { class: "col-review", text: `Review: ${column.review}` }));
    host.append(row);
  }

  if (editable.length > 120) {
    host.append(el("p", { class: "hint", text: `Showing 120 of ${editable.length} — filter to narrow it down.` }));
  }
}

function numberInput(target, key) {
  return el("input", {
    type: "number", step: "any", value: target[key],
    onchange: (event) => { target[key] = parseFloat(event.target.value); validate(); refreshYaml(); },
  });
}

function labelled(title, sub, control) {
  return el("label", { class: "field" }, [
    el("span", { class: "lbl", text: title }),
    control,
    sub ? el("span", { class: "sub", text: sub }) : null,
  ]);
}

function slider(value, min, max, step, onCommit, format) {
  const output = el("output", { text: format(value) });
  const input = el("input", {
    type: "range", min, max, step, value,
    oninput: (event) => { output.textContent = format(parseFloat(event.target.value)); },
    onchange: (event) => { onCommit(parseFloat(event.target.value)); },
  });
  return el("div", { class: "slider" }, [input, output]);
}

/* --------------------------------------------------------------- raw YAML */

async function refreshYaml() {
  try {
    const { yaml } = await api("/api/spec/yaml", { method: "POST", body: JSON.stringify(state.spec) });
    $("#cfg-yaml").value = yaml;
  } catch { /* the form remains the source of truth */ }
}

function wireYaml() {
  $("#btn-yaml-apply").onclick = async () => {
    const target = $("#yaml-status");
    try {
      const result = await api("/api/spec/parse", { method: "POST", body: JSON.stringify({ yaml: $("#cfg-yaml").value }) });
      if (!result.valid) {
        target.replaceChildren(el("div", { class: "note bad", html:
          `<strong>Not applied.</strong><ul>${result.problems.map((p) => `<li>${p}</li>`).join("")}</ul>` }));
        return;
      }
      state.spec = result.parsed;
      target.replaceChildren(el("div", { class: "note good", text: "Applied. The forms above now reflect this document." }));
      await validate();
      renderConfigure();
    } catch (error) {
      target.replaceChildren(el("div", { class: "note bad", text: error.message }));
    }
  };
  $("#btn-yaml-revert").onclick = () => { $("#yaml-status").replaceChildren(); refreshYaml(); };
}

/* --------------------------------------------------------------- 4. results */

async function startRun() {
  $("#run-progress-card").hidden = false;
  $("#results-body").hidden = true;
  $("#run-error").replaceChildren();
  reach("results");
  show("results");

  try {
    const { job } = await api("/api/run", {
      method: "POST",
      body: JSON.stringify({
        spec: state.spec,
        num_records: parseInt($("#cfg-records").value, 10) || 10000,
        seed: parseInt($("#cfg-seed").value, 10) || 42,
        periods: parseInt($("#cfg-periods").value, 10) || null,
        scenario: $("#cfg-scenario").value || null,
      }),
    });
    state.job = job;
    poll();
  } catch (error) {
    $("#run-progress-card").hidden = true;
    $("#run-error").replaceChildren(el("div", { class: "note bad", text: error.message }));
  }
}

async function poll() {
  if (!state.job) return;
  let job;
  try {
    job = await api(`/api/run/${state.job}`);
  } catch (error) {
    $("#run-error").replaceChildren(el("div", { class: "note bad", text: error.message }));
    return;
  }

  $("#run-stage").textContent = job.stage || job.status;
  $("#run-pct").textContent = fmt.pct(job.progress, 0);
  $("#run-bar").style.width = `${(job.progress * 100).toFixed(1)}%`;

  if (job.status === "error") {
    $("#run-progress-card").hidden = true;
    $("#run-error").replaceChildren(el("div", { class: "note bad", text: job.error }));
    status("Run failed.", "bad");
    return;
  }
  if (job.status === "done") {
    $("#run-progress-card").hidden = true;
    state.result = job.result;
    renderResults(job.result);
    status("Run complete.", "good");
    return;
  }
  setTimeout(poll, 450);
}

function renderResults(result) {
  $("#results-body").hidden = false;
  const validation = result.validation;
  const survival = result.entities ? result.surviving_entities / result.entities : 0;

  $("#run-stats").replaceChildren(
    stat("Entities", fmt.int(result.entities)),
    stat("Periods", fmt.int(result.periods)),
    stat("Survived", fmt.pct(survival, 1)),
    stat("Rows", fmt.int(result.mix.reduce((sum, m) => sum + m.rows, 0))),
    stat("Time", `${result.timings.total_seconds.toFixed(1)}s`),
    stat("Spec", result.spec_hash, true),
  );

  const target = $("#run-validation");
  target.replaceChildren();
  if (validation) {
    const failed = validation.checks.filter((c) => !c.passed);
    target.append(el("div", { class: `note ${validation.passed ? "good" : "bad"}`, html:
      `<strong>${validation.total - validation.failed}/${validation.total} invariants passed</strong> — ` +
      (validation.passed
        ? "the panel is consistent with everything the spec asserts."
        : `<ul>${failed.map((c) => `<li><code class="inline">${c.name}</code> — ${fmt.int(c.violations)} violating row(s)</li>`).join("")}</ul>`) }));
  }
  if (result.scenario) {
    target.append(el("div", { class: "note", html: `Scenario overlay applied: <strong>${result.scenario}</strong>.` }));
  }

  renderMixChart(result.mix, state.spec.lifecycle);
  renderFiles(result);
  loadPreview(result.panel || result.files[0]);
}

function renderFiles(result) {
  const files = [];
  if (result.panel) files.push({ path: result.panel, label: "Consolidated panel" });
  for (const path of result.files) files.push({ path, label: "Per-period tape" });

  const table = $("#run-files");
  table.replaceChildren(
    el("thead", {}, el("tr", {}, [
      el("th", { text: "File" }), el("th", { text: "What it is" }), el("th", { text: "" }),
    ])),
    el("tbody", {}, files.slice(0, 40).map((file) =>
      el("tr", {}, [
        el("td", {}, el("code", { class: "inline", text: file.path.split("/").pop() })),
        el("td", { text: file.label }),
        el("td", {}, el("a", { href: `/api/download?path=${encodeURIComponent(file.path)}`, text: "Download" })),
      ])
    ))
  );
  if (files.length > 40) {
    table.append(el("tfoot", {}, el("tr", {}, el("td", { colspan: 3 },
      el("span", { class: "hint", text: `…and ${files.length - 40} more in .sdd-workspace.` })))));
  }
}

async function loadPreview(path) {
  if (!path) return;
  $("#preview-hint").textContent = `First rows of ${path.split("/").pop()}.`;
  try {
    const { columns, rows } = await api(`/api/preview?path=${encodeURIComponent(path)}&rows=12`);
    $("#run-preview").replaceChildren(
      el("thead", {}, el("tr", {}, columns.map((c) => el("th", { text: c })))),
      el("tbody", {}, rows.map((row) =>
        el("tr", {}, row.map((cell) =>
          el("td", { class: typeof cell === "number" ? "num" : "", text: cell === null ? "" : String(cell) })))))
    );
  } catch (error) {
    $("#run-preview").replaceChildren(el("tbody", {}, el("tr", {}, el("td", { text: error.message }))));
  }
}

/* ----------------------------------------------------------------- chart */

// Ordered best-first to match the spec's own state ordering, so the stack
// reads healthy-at-the-bottom and distressed-on-top.
const PALETTE = ["#3f8f7f", "#7fae56", "#d8b34a", "#dd9040", "#c9603c", "#a63a4a", "#6d4a7c", "#8d8b84"];

function renderMixChart(mix, lifecycle) {
  const host = $("#mix-chart");
  host.replaceChildren();
  if (!mix?.length) return;

  const states = (lifecycle?.states || []).filter((name) => mix.some((m) => m[name]));
  for (const period of mix) {
    for (const key of Object.keys(period)) {
      if (!["period", "date", "rows"].includes(key) && !states.includes(key)) states.push(key);
    }
  }

  const W = 900, H = 300, PAD = { t: 12, r: 12, b: 34, l: 58 };
  const innerW = W - PAD.l - PAD.r;
  const innerH = H - PAD.t - PAD.b;
  const peak = Math.max(...mix.map((m) => m.rows));
  const x = (i) => PAD.l + (mix.length === 1 ? innerW / 2 : (i / (mix.length - 1)) * innerW);
  const y = (v) => PAD.t + innerH - (v / peak) * innerH;

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "chart");
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  const ns = (tag, attrs) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  };

  // gridlines
  for (let step = 0; step <= 4; step++) {
    const value = (peak / 4) * step;
    svg.append(ns("line", { x1: PAD.l, x2: W - PAD.r, y1: y(value), y2: y(value), class: "axis" }));
    const label = ns("text", { x: PAD.l - 8, y: y(value) + 3, "text-anchor": "end" });
    label.textContent = value >= 1000 ? `${Math.round(value / 1000)}k` : Math.round(value);
    svg.append(label);
  }

  // stacked areas, healthy at the bottom
  let baseline = mix.map(() => 0);
  states.forEach((name, index) => {
    const tops = mix.map((period, i) => baseline[i] + (period[name] || 0));
    const forward = mix.map((_, i) => `${x(i)},${y(tops[i])}`).join(" ");
    const back = mix.map((_, i) => `${x(mix.length - 1 - i)},${y(baseline[mix.length - 1 - i])}`).join(" ");
    svg.append(ns("polygon", {
      points: `${forward} ${back}`,
      fill: PALETTE[index % PALETTE.length],
      "fill-opacity": 0.9,
    }));
    baseline = tops;
  });

  // x labels, thinned so they never collide
  const every = Math.max(1, Math.ceil(mix.length / 8));
  mix.forEach((period, i) => {
    if (i % every && i !== mix.length - 1) return;
    const label = ns("text", { x: x(i), y: H - 12, "text-anchor": "middle" });
    label.textContent = period.date.slice(0, 7);
    svg.append(label);
  });

  host.append(svg);
  host.append(el("div", { class: "legend" }, states.map((name, index) =>
    el("span", {}, [
      el("i", { style: `background:${PALETTE[index % PALETTE.length]}` }),
      name,
    ]))));
}

/* ------------------------------------------------------------------- wire */

function wireNav() {
  $$(".step").forEach((button) => {
    button.onclick = () => { if (state.reachable[button.dataset.view]) show(button.dataset.view); };
  });
  $("#btn-back").onclick = () => {
    const order = VIEWS.filter((v) => state.reachable[v]);
    const at = order.indexOf(state.view);
    show(order[Math.max(0, at - 1)]);
  };
  $("#btn-next").onclick = () => {
    if (state.view === "configure") return startRun();
    if (state.view === "results") return show("configure");
    const order = VIEWS.filter((v) => state.reachable[v]);
    const at = order.indexOf(state.view);
    show(order[Math.min(order.length - 1, at + 1)]);
  };
  $("#cfg-filter").oninput = renderColumns;
}

async function boot() {
  wireNav();
  wireUpload();
  wireYaml();
  try {
    await loadPacks();
  } catch (error) {
    status(`Could not reach the server: ${error.message}`, "bad");
  }
  show("source");
}

boot();
