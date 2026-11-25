import { api, AuthError, uuid } from "./api.js";
import { barChart, dayTs, disposeAll, esc, isoDay, METRIC_SLOT, seriesColor, timeChart } from "./charts.js";

// --- small helpers -----------------------------------------------------------------

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const view = $("#view");
const state = { status: null, metrics: [], categories: [], range: { trends: "28", longterm: "week" } };

const today = () => isoDay(Date.now());
const addDays = (d, n) => isoDay(dayTs(d) + n * 86400000 + 3600000 * 2);
const hm = (sec) => { const m = Math.round(sec / 60); return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`; };
const hours = (sec) => sec / 3600;
const round = (v, d = 0) => (v == null ? "–" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d }));
const avg = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
const ago = (iso) => {
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  if (min < 60 * 36) return `${Math.round(min / 60)} h ago`;
  return `${Math.round(min / 1440)} days ago`;
};
const localInput = (date) => {
  const d = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return d.toISOString().slice(0, 16);
};
const fromInput = (v) => (v ? new Date(v).toISOString() : null);
const fmtDateTime = (iso) => new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
const fmtDate = (iso) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

function toast(msg, ms = 3500) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => (t.hidden = true), ms);
}

async function guard(fn) {
  try { return await fn(); } catch (e) {
    if (e instanceof AuthError) { renderLogin(); return undefined; }
    toast(e.message || String(e), 6000); console.error(e); return undefined;
  }
}

/** A <dialog> with a form. ``body`` is trusted markup built with esc(). Resolves to FormData or null. */
function dialog(title, body, { submit = "Save", danger = null } = {}) {
  const dlg = $("#dialog"), form = $("#dialog-form");
  form.innerHTML = `<h3>${esc(title)}</h3>${body}
    <div class="actions">
      ${danger ? `<button class="btn danger spacer" value="danger" type="submit" formnovalidate>${esc(danger)}</button>` : ""}
      <button class="btn" value="cancel" type="submit" formnovalidate>Cancel</button>
      ${submit ? `<button class="btn primary" value="ok" type="submit">${esc(submit)}</button>` : ""}
    </div>`;
  dlg.showModal();
  return new Promise((resolve) => {
    dlg.onclose = () => {
      const v = dlg.returnValue;
      resolve(v === "ok" ? new FormData(form) : v === "danger" ? "danger" : null);
    };
  });
}

// --- data -------------------------------------------------------------------------------

async function daily(keys, from, to) {
  const series = await api.daily(keys, from, to);
  const out = {};
  for (const s of series) {
    // prefer garmin over other sources when the same metric exists twice
    if (!out[s.key] || s.source === "garmin") out[s.key] = s.points;
  }
  return out;
}

const metricName = (key) => state.metrics.find((m) => m.key === key)?.name ?? key;

const pts = (points, f = (v) => v) => (points || []).map(([d, v]) => [dayTs(d), typeof v === "number" ? f(v) : v]);

async function loadEvents(from, to) {
  return (await api.events(from, to)) || [];
}

function chartCard(title, legend, { tall = false, table = null, unit = "" } = {}) {
  const card = document.createElement("section");
  card.className = "card chart-card";
  const legendHtml = legend.length > 1
    ? `<span class="legend">${legend.map((l) => `<span><i class="${l.line ? "line" : ""}" style="background:${seriesColor(l.slot)}"></i>${esc(l.name)}</span>`).join("")}</span>`
    : "";
  card.innerHTML = `<header><h3>${esc(title)}${unit ? ` <span class="muted small">${esc(unit)}</span>` : ""}</h3>${legendHtml}
    ${table ? `<button class="btn small" type="button" data-table style="margin-left:auto">Table</button>` : ""}</header>
    <div class="chart ${tall ? "tall" : ""}"></div><div class="table-wrap" hidden></div>`;
  if (table) {
    $("[data-table]", card).onclick = () => {
      const wrap = $(".table-wrap", card);
      if (wrap.hidden && !wrap.innerHTML) {
        const { head, rows } = table();
        wrap.innerHTML = `<table><thead><tr>${head.map((h, i) => `<th class="${i ? "num" : ""}">${esc(h)}</th>`).join("")}</tr></thead>
          <tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td class="${i ? "num" : ""}">${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
        wrap.style.maxHeight = "320px";
      }
      wrap.hidden = !wrap.hidden;
      $(".chart", card).hidden = !wrap.hidden;
      if (wrap.hidden) echarts.getInstanceByDom($(".chart", card))?.resize();
    };
  }
  return card;
}

/** Build the table() callback for a set of day-series. */
const seriesTable = (defs) => () => {
  const days = [...new Set(defs.flatMap((d) => (d.points || []).map(([day]) => day)))].sort().reverse();
  const maps = defs.map((d) => new Map(d.points || []));
  return {
    head: ["Date", ...defs.map((d) => d.name)],
    rows: days.map((day) => [day, ...maps.map((m, i) => (m.has(day) ? defs[i].fmt(m.get(day)) : "–"))]),
  };
};

// --- header: sync status & refresh --------------------------------------------------------

async function refreshStatus() {
  const st = await api.status().catch((e) => { if (e instanceof AuthError) throw e; return null; });
  if (!st) return null;
  state.status = st;
  const src = st.sources[0];
  const pill = $("#sync-pill"), label = $(".label", pill), banner = $("#banner"), btn = $("#refresh-btn");
  $(".demo-badge").hidden = !st.demo;
  const run = src.last_run;
  const failed = run && run.status !== "ok" && run.status !== "running"
    && (!src.last_success_at || run.started_at > src.last_success_at);
  let s, text;
  if (src.syncing || run?.status === "running" && run.kind !== "backfill") { s = "busy"; text = "Fetching…"; }
  else if (failed) {
    s = "error";
    text = run.status === "auth_required" ? "Garmin login needed" : run.status === "rate_limited" ? "Garmin rate limit" : "Last sync failed";
  } else if (!src.last_success_at) { s = "stale"; text = "Never synced"; }
  else {
    const hoursOld = (Date.now() - new Date(src.last_success_at)) / 3600000;
    s = hoursOld > 36 ? "stale" : "ok";
    text = `Updated ${ago(src.last_success_at)}`;
  }
  pill.dataset.state = s;
  label.textContent = text;
  pill.title = src.last_success_at ? `Last successful sync: ${fmtDateTime(src.last_success_at)}` : "No successful sync yet";
  if (failed) {
    const hint = run.status === "auth_required"
      ? "Run <code>pulse-mirror login garmin</code> in the container (see docs/nas-setup.md)." : "";
    banner.innerHTML = `<b>Garmin sync problem</b> (${esc(ago(run.started_at))}): ${esc(run.error_message || run.status)} ${hint}
      ${src.last_success_at ? `<div class="small">Last successful sync: ${esc(fmtDateTime(src.last_success_at))}</div>` : ""}`;
    banner.hidden = false;
  } else banner.hidden = true;
  const cooling = src.cooldown_until && new Date(src.cooldown_until) > new Date();
  btn.disabled = st.demo || s === "busy" || cooling;
  btn.classList.toggle("spinning", s === "busy");
  btn.title = st.demo ? "Disabled in the demo"
    : cooling ? `Cooldown until ${new Date(src.cooldown_until).toLocaleTimeString()}` : "Fetch new data from Garmin Connect";
  return st;
}

async function onRefresh() {
  await guard(async () => {
    await api.sync();
    toast("Fetching from Garmin Connect… (only what your watch has already synced)");
    const started = Date.now();
    await refreshStatus();
    const poll = setInterval(async () => {
      const st = await refreshStatus();
      if (!st?.sources[0].syncing || Date.now() - started > 10 * 60000) {
        clearInterval(poll);
        const run = st?.sources[0].last_run;
        toast(run?.status === "ok" ? "Up to date." : `Sync: ${run?.error_message || run?.status}`);
        route();
      }
    }, 3000);
  });
}

// --- views ------------------------------------------------------------------------------------

const TILES = [
  { key: "resting_hr", unit: "bpm", lowerIsBetter: true },
  { key: "hrv_overnight_avg", unit: "ms" },
  { key: "sleep_duration", fmt: hm, avgFmt: hm },
  { key: "sleep_score" },
  { key: "body_battery_max", label: "Body Battery (high)" },
  { key: "stress_avg", lowerIsBetter: true },
  { key: "vo2max_cycling", digits: 1 },
  { key: "training_load_acute", label: "Acute load" },
];

async function viewToday() {
  const to = today(), from = addDays(to, -40);
  const [data, events, scales, ratings] = await Promise.all([
    daily([...TILES.map((t) => t.key), "hrv_status", "training_status", "sleep_deep", "sleep_rem"], from, to),
    loadEvents(addDays(to, -14), to),
    api.scales(),
    api.ratings(addDays(to, -1), to),
  ]);
  const tiles = TILES.map((t) => {
    const points = data[t.key] || [];
    if (!points.length) return "";
    const [lastDay, value] = points[points.length - 1];
    const before = points.filter(([d]) => d < lastDay);
    const within = (n) => before.filter(([d]) => d >= addDays(lastDay, -n)).map(([, v]) => v);
    const a7 = avg(within(7)), a28 = avg(within(28));
    const f = t.fmt || ((v) => round(v, t.digits || 0));
    const af = t.avgFmt || ((v) => round(v, t.digits ? t.digits : 0));
    let delta = a28 != null ? value - a28 : null;
    if (delta != null && Math.abs(delta) < (t.fmt === hm ? 60 : 0.5 * 10 ** -(t.digits || 0))) delta = 0;
    const deltaTxt = delta == null ? "" : t.fmt === hm ? `${delta > 0 ? "+" : delta < 0 ? "−" : "±"}${hm(Math.abs(delta))}` : `${delta > 0 ? "+" : delta < 0 ? "−" : "±"}${round(Math.abs(delta), t.digits || 0)}`;
    const extra = t.key === "hrv_overnight_avg" && data.hrv_status?.length ? data.hrv_status.at(-1)[1].toLowerCase()
      : t.key === "training_load_acute" && data.training_status?.length ? data.training_status.at(-1)[1].toLowerCase() : "";
    return `<div class="card tile">
      <div class="label">${esc(t.label || metricName(t.key))}</div>
      <div class="value">${esc(f(value))}${t.unit ? `<small>${esc(t.unit)}</small>` : ""}</div>
      <div class="base">
        <span>7 d ⌀ ${esc(a7 == null ? "–" : af(a7))} · 28 d ⌀ ${esc(a28 == null ? "–" : af(a28))}</span>
        ${delta == null ? "" : `<span>${esc(deltaTxt)} vs 28-day baseline</span>`}
        ${extra ? `<span class="chip">${esc(extra)}</span>` : ""}
      </div>
      <div class="date">${esc(lastDay === to ? "today" : fmtDate(lastDay))}</div>
    </div>`;
  }).join("");

  const ratedToday = ratings.some((r) => r.recorded_at.slice(0, 10) === to || isoDay(new Date(r.recorded_at)) === to);
  const catById = new Map(state.categories.map((c) => [c.id, c]));
  view.innerHTML = `
    <h2>Today <span class="muted small">compared with your own 7- and 28-day baselines</span></h2>
    ${tiles ? `<div class="grid tiles">${tiles}</div>` : `<div class="card empty">No data yet. Run a sync or the backfill.</div>`}
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));margin-top:12px">
      <section class="card">
        <h3>Wellbeing</h3>
        <p class="muted small">${ratedToday ? "You've rated today. Thanks!" : scales.length ? "No rating yet today." : "No scales defined yet (Settings)."}</p>
        ${scales.length ? `<button class="btn primary" data-rate>Rate my wellbeing</button>` : ""}
      </section>
      <section class="card">
        <h3>Recent events</h3>
        <div class="list" style="margin-top:8px">
          ${events.length ? events.slice(-5).reverse().map((e) => `<div class="item">
            <span class="swatch" style="background:${esc(catById.get(e.category_id)?.color)}"></span>
            <div class="grow"><div class="title">${esc(e.title)}</div>
            <div class="muted small">${esc(fmtDateTime(e.start_at))}${e.end_at ? " – " + esc(fmtDateTime(e.end_at)) : ""}</div></div></div>`).join("")
            : `<p class="muted small">Nothing in the last two weeks. Use + to mark something.</p>`}
        </div>
      </section>
    </div>`;
  $("[data-rate]", view)?.addEventListener("click", () => ratingDialog());
}

const RANGES = [["28", "4 W"], ["91", "3 M"], ["182", "6 M"], ["365", "1 Y"], ["all", "All"]];

function rangeToolbar(current, onPick, options = RANGES) {
  const bar = document.createElement("div");
  bar.className = "toolbar";
  bar.innerHTML = `<div class="seg" role="group">${options.map(([v, l]) =>
    `<button type="button" data-v="${v}" aria-pressed="${v === current}">${esc(l)}</button>`).join("")}</div>`;
  $$("button", bar).forEach((b) => (b.onclick = () => onPick(b.dataset.v)));
  return bar;
}

const rangeFrom = (r) => (r === "all" ? null : addDays(today(), -Number(r) + 1));

async function viewTrends() {
  const r = state.range.trends, from = rangeFrom(r), to = today();
  const keys = ["resting_hr", "hrv_overnight_avg", "hrv_baseline_low", "hrv_baseline_high", "sleep_duration",
    "sleep_score", "body_battery_max", "body_battery_min", "stress_avg", "training_load_acute", "training_load_chronic"];
  const [data, events] = await Promise.all([daily(keys, from, to), loadEvents(from, to)]);
  view.innerHTML = "<h2>Trends</h2>";
  view.append(rangeToolbar(r, (v) => { state.range.trends = v; route(); }));
  const box = document.createElement("div");
  box.className = "charts";
  view.append(box);
  const n0 = (k) => (data[k] || []).map(([d, v]) => [d, v]);
  const defs = [
    { title: "Resting heart rate", unit: "bpm", series: [{ key: "resting_hr", name: "Resting HR", slot: 8 }] },
    { title: "HRV (overnight)", unit: "ms", series: [
      { key: "hrv_overnight_avg", name: "HRV", slot: 1 },
      { key: "hrv_baseline_low", name: "Baseline low", slot: 7, dashed: true },
      { key: "hrv_baseline_high", name: "Baseline high", slot: 7, dashed: true }] },
    { title: "Sleep", unit: "h", series: [{ key: "sleep_duration", name: "Sleep", slot: 7, type: "bar", f: hours }],
      fmt: (v) => hm(v * 3600), yMin: 0 },
    { title: "Sleep score", series: [{ key: "sleep_score", name: "Sleep score", slot: 3 }] },
    { title: "Body Battery", series: [
      { key: "body_battery_max", name: "High", slot: 6 }, { key: "body_battery_min", name: "Low", slot: 3 }], yMin: 0, yMax: 100 },
    { title: "Stress (average)", series: [{ key: "stress_avg", name: "Stress", slot: 2 }] },
    { title: "Training load", series: [
      { key: "training_load_acute", name: "Acute", slot: 2 }, { key: "training_load_chronic", name: "Chronic", slot: 1, dashed: true }], yMin: 0 },
  ];
  defs.forEach((d, i) => {
    const fmtCell = d.fmt ? (v) => d.fmt(v) : (v) => round(v);
    const card = chartCard(d.title, d.series.map((s) => ({ name: s.name, slot: s.slot, line: s.type !== "bar" })), {
      unit: d.unit, table: seriesTable(d.series.map((s) => ({ name: s.name, points: n0(s.key).map(([day, v]) => [day, s.f ? s.f(v) : v]), fmt: fmtCell }))),
    });
    box.append(card);
    timeChart($(".chart", card), {
      series: d.series.map((s) => ({ ...s, points: pts(data[s.key], s.f) })),
      unit: d.unit, format: d.fmt, yMin: d.yMin, yMax: d.yMax,
      events, categories: state.categories, onEventClick: editEvent,
      group: "trends", zoom: i === defs.length - 1,
      min: from ? dayTs(from) : undefined, max: dayTs(to) + 86400000,
    });
  });
}

function aggregate(points, period, how = "avg") {
  const buckets = new Map();
  for (const [d, v] of points || []) {
    if (typeof v !== "number") continue;
    const dt = new Date(dayTs(d));
    let key;
    if (period === "month") key = `${d.slice(0, 7)}-01`;
    else { dt.setDate(dt.getDate() - ((dt.getDay() + 6) % 7)); key = isoDay(dt.getTime()); }
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(v);
  }
  return [...buckets.entries()].sort().map(([k, vs]) =>
    [k, how === "sum" ? vs.reduce((a, b) => a + b, 0) : how === "last" ? vs.at(-1) : avg(vs)]);
}

async function viewLongterm() {
  const period = state.range.longterm;
  const keys = ["resting_hr", "hrv_overnight_avg", "vo2max_cycling", "vo2max_running", "sleep_duration", "training_load_chronic"];
  const [data, acts, events] = await Promise.all([daily(keys), api.activities(), loadEvents()]);
  view.innerHTML = "<h2>Long-term</h2>";
  view.append(rangeToolbar(period, (v) => { state.range.longterm = v; route(); }, [["week", "Weekly"], ["month", "Monthly"]]));
  const box = document.createElement("div");
  box.className = "charts";
  view.append(box);
  const rideHours = new Map();
  for (const a of acts) rideHours.set(a.local_date, (rideHours.get(a.local_date) || 0) + (a.duration_s || 0) / 3600);
  const volume = aggregate([...rideHours.entries()], period, "sum");
  const label = period === "week" ? "weekly" : "monthly";
  const defs = [
    { title: `Resting heart rate (${label} ⌀)`, unit: "bpm", series: [{ name: "Resting HR", slot: 8, agg: aggregate(data.resting_hr, period) }] },
    { title: `HRV (${label} ⌀)`, unit: "ms", series: [{ name: "HRV", slot: 1, agg: aggregate(data.hrv_overnight_avg, period) }] },
    { title: "VO2max", unit: "ml/kg/min", digits: 1, series: [
      { name: "Cycling", slot: 1, agg: aggregate(data.vo2max_cycling, period, "last") },
      { name: "Running", slot: 2, agg: aggregate(data.vo2max_running, period, "last") }] },
    { title: `Sleep (${label} ⌀)`, unit: "h", fmt: (v) => hm(v * 3600), series: [
      { name: "Sleep", slot: 7, agg: aggregate(data.sleep_duration, period).map(([d, v]) => [d, v / 3600]) }] },
    { title: `Training volume (${label} hours)`, unit: "h", yMin: 0, series: [{ name: "Hours", slot: 3, type: "bar", agg: volume }] },
    { title: `Chronic training load (${label} ⌀)`, yMin: 0, series: [{ name: "Chronic load", slot: 1, agg: aggregate(data.training_load_chronic, period) }] },
  ];
  for (const d of defs) {
    const fmt = d.fmt || ((v) => round(v, d.digits || 0));
    const card = chartCard(d.title, d.series.map((s) => ({ name: s.name, slot: s.slot, line: s.type !== "bar" })), {
      tall: true, unit: d.unit, table: seriesTable(d.series.map((s) => ({ name: s.name, points: s.agg, fmt }))),
    });
    box.append(card);
    timeChart($(".chart", card), {
      series: d.series.map((s) => ({ ...s, points: s.agg.map(([k, v]) => [dayTs(k), v]) })),
      unit: d.unit, format: fmt, yMin: d.yMin, events, categories: state.categories, onEventClick: editEvent,
      group: "long", zoom: true,
    });
  }
}

async function viewActivities() {
  const r = state.range.activities || "365";
  const from = rangeFrom(r);
  const acts = await api.activities(from, today());
  const types = [...new Set(acts.map((a) => a.type))].sort();
  const type = state.activityType && types.includes(state.activityType) ? state.activityType : "";
  const list = acts.filter((a) => !type || a.type === type);
  const sum = (f) => list.reduce((s, a) => s + (a[f] || 0), 0);
  view.innerHTML = `<h2>Activities</h2>`;
  const bar = rangeToolbar(r, (v) => { state.range.activities = v; route(); });
  const sel = document.createElement("select");
  sel.innerHTML = `<option value="">All types</option>${types.map((t) => `<option ${t === type ? "selected" : ""}>${esc(t)}</option>`).join("")}`;
  sel.onchange = () => { state.activityType = sel.value; route(); };
  bar.append(sel);
  view.append(bar);
  const summary = document.createElement("div");
  summary.className = "grid tiles";
  summary.innerHTML = [
    ["Activities", list.length.toLocaleString()],
    ["Time", `${round(sum("duration_s") / 3600)} h`],
    ["Distance", `${round(sum("distance_m") / 1000)} km`],
    ["Elevation", `${round(sum("elevation_gain_m"))} m`],
  ].map(([l, v]) => `<div class="card tile"><div class="label">${l}</div><div class="value">${esc(v)}</div></div>`).join("");
  view.append(summary);

  const monthly = aggregate(list.map((a) => [a.local_date, (a.duration_s || 0) / 3600]), "month", "sum");
  const card = chartCard("Hours per month", [{ name: "Hours", slot: 3 }]);
  card.style.marginTop = "12px";
  view.append(card);
  barChart($(".chart", card), { labels: monthly.map(([k]) => k.slice(0, 7)), values: monthly.map(([, v]) => Math.round(v * 10) / 10), slot: 3, unit: "h" });

  const table = document.createElement("section");
  table.className = "card table-wrap";
  table.style.marginTop = "12px";
  table.innerHTML = list.length ? `<table><thead><tr><th>Date</th><th>Type</th><th>Name</th><th class="num">Time</th>
    <th class="num">km</th><th class="num">↑ m</th><th class="num">⌀ HR</th><th class="num">⌀ W</th><th class="num">NP</th><th class="num">Load</th></tr></thead>
    <tbody>${list.slice(0, 500).map((a) => `<tr><td>${esc(a.local_date)}</td><td>${esc(a.type)}</td><td>${esc(a.name || "")}</td>
      <td class="num">${a.duration_s ? esc(hm(a.duration_s)) : "–"}</td><td class="num">${esc(round(a.distance_m / 1000, 1))}</td>
      <td class="num">${esc(round(a.elevation_gain_m))}</td><td class="num">${esc(round(a.avg_hr))}</td>
      <td class="num">${esc(round(a.avg_power))}</td><td class="num">${esc(round(a.norm_power))}</td>
      <td class="num">${esc(round(a.training_load))}</td></tr>`).join("")}</tbody></table>`
    : `<div class="empty">No activities in this range.</div>`;
  view.append(table);
}

const OVERLAYS = [["", "No Garmin overlay"], ["hrv_overnight_avg", "HRV"], ["resting_hr", "Resting HR"],
  ["sleep_duration", "Sleep duration"], ["sleep_score", "Sleep score"], ["body_battery_max", "Body Battery (high)"],
  ["stress_avg", "Stress"], ["training_load_acute", "Acute load"]];

async function viewWellbeing() {
  const r = state.range.wellbeing || "91", from = rangeFrom(r), to = today();
  const [scales, ratings, events] = await Promise.all([api.scales(true), api.ratings(from, to), loadEvents(from, to)]);
  const active = scales.filter((s) => !s.archived);
  state.wbScales ??= new Set(active.slice(0, 1).map((s) => s.id));
  const overlay = state.overlay ?? "hrv_overnight_avg";
  const data = overlay ? await daily([overlay], from, to) : {};
  view.innerHTML = `<h2>Wellbeing</h2>`;
  const bar = rangeToolbar(r, (v) => { state.range.wellbeing = v; route(); });
  const sel = document.createElement("select");
  sel.innerHTML = OVERLAYS.map(([k, l]) => `<option value="${k}" ${k === overlay ? "selected" : ""}>${esc(l)}</option>`).join("");
  sel.onchange = () => { state.overlay = sel.value; route(); };
  const rate = document.createElement("button");
  rate.className = "btn primary"; rate.textContent = "Rate now"; rate.style.marginLeft = "auto";
  rate.onclick = () => ratingDialog();
  bar.append(sel, rate);
  view.append(bar);
  if (!scales.length) {
    view.insertAdjacentHTML("beforeend", `<div class="card empty">No scales yet. Create your scales in Settings.</div>`);
    return;
  }
  const chips = document.createElement("div");
  chips.className = "toolbar";
  chips.innerHTML = scales.map((s, i) => `<label class="chip"><input type="checkbox" data-scale="${s.id}" ${state.wbScales.has(s.id) ? "checked" : ""}>
    <i style="width:10px;height:3px;border-radius:2px;background:${seriesColor((i % 6) + 2)}"></i>${esc(s.name)}${s.archived ? " (archived)" : ""}</label>`).join("");
  $$("input", chips).forEach((c) => (c.onchange = () => {
    const id = Number(c.dataset.scale);
    c.checked ? state.wbScales.add(id) : state.wbScales.delete(id); route();
  }));
  view.append(chips);

  // Indexed to 0–100 % so ratings and Garmin values share one honest axis.
  const series = [];
  scales.forEach((s, i) => {
    if (!state.wbScales.has(s.id)) return;
    const mine = ratings.filter((x) => x.scale_id === s.id);
    series.push({
      name: s.name, slot: (i % 6) + 2, showSymbol: true,
      points: mine.map((x) => [new Date(x.recorded_at).getTime(), ((x.value - s.min_value) / (s.max_value - s.min_value)) * 100, `${round(x.value, 1)} / ${round(s.max_value)}`]),
    });
  });
  if (overlay && data[overlay]?.length) {
    const vals = data[overlay].map(([, v]) => v);
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const f = overlay === "sleep_duration" ? hm : (v) => round(v);
    series.push({
      name: metricName(overlay), slot: METRIC_SLOT[overlay] || 1, dashed: true,
      points: data[overlay].map(([d, v]) => [dayTs(d) + 6 * 3600000, hi > lo ? ((v - lo) / (hi - lo)) * 100 : 50, f(v)]),
    });
  }
  const card = chartCard("Ratings vs Garmin (indexed: each series scaled to its own range)", series.map((s) => ({ name: s.name, slot: s.slot, line: true })), { tall: true });
  view.append(card);
  timeChart($(".chart", card), {
    series, unit: "% of range", yMin: 0, yMax: 100, format: (v) => `${Math.round(v)}`,
    formatTooltip: (value) => value[2] ?? round(value[1]),
    events, categories: state.categories, onEventClick: editEvent, zoom: true,
    min: from ? dayTs(from) : undefined, max: dayTs(to) + 86400000,
  });

  const byScale = new Map(scales.map((s) => [s.id, s]));
  // one row per check-in (ratings saved together share recorded_at)
  const checkins = new Map();
  for (const x of ratings) {
    if (!checkins.has(x.recorded_at)) checkins.set(x.recorded_at, []);
    checkins.get(x.recorded_at).push(x);
  }
  const rows = [...checkins.entries()].sort((a, b) => b[0].localeCompare(a[0])).slice(0, 20);
  const list = document.createElement("section");
  list.className = "card table-wrap";
  list.style.marginTop = "12px";
  list.innerHTML = `<h3>Recent check-ins</h3>${rows.length ? `<table><thead><tr><th>When</th>
    ${scales.map((s) => `<th class="num">${esc(s.name)}</th>`).join("")}<th>Note</th></tr></thead><tbody>
    ${rows.map(([when, xs]) => `<tr><td>${esc(fmtDateTime(when))}</td>${scales.map((s) => {
      const x = xs.find((r) => r.scale_id === s.id);
      return `<td class="num">${x ? `<button class="btn small" data-edit="${esc(x.id)}" title="Edit">${esc(round(x.value, 1))}</button>` : "–"}</td>`;
    }).join("")}<td>${esc(xs.find((r) => r.note)?.note || "")}</td></tr>`).join("")}</tbody></table>`
    : `<p class="muted">No ratings in this range.</p>`}`;
  $$("[data-edit]", list).forEach((b) => (b.onclick = () => editRating(ratings.find((x) => x.id === b.dataset.edit), byScale)));
  view.append(list);
}

async function viewEvents() {
  const events = (await loadEvents()).reverse();
  const cats = state.categories;
  const filter = state.eventCat || "";
  const catById = new Map(cats.map((c) => [c.id, c]));
  view.innerHTML = `<h2>Events</h2>`;
  const bar = document.createElement("div");
  bar.className = "toolbar";
  bar.innerHTML = `<select data-filter><option value="">All categories</option>${cats.map((c) => `<option value="${c.id}" ${String(c.id) === filter ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
    <button class="btn primary" data-new style="margin-left:auto">New event</button>`;
  $("[data-filter]", bar).onchange = (e) => { state.eventCat = e.target.value; route(); };
  $("[data-new]", bar).onclick = () => eventDialog(presetEvent("now"));
  view.append(bar);
  const shown = events.filter((e) => !filter || String(e.category_id) === filter);
  const list = document.createElement("section");
  list.className = "card list";
  list.innerHTML = shown.length ? shown.map((e) => {
    const c = catById.get(e.category_id);
    const days = e.end_at ? Math.max(1, Math.round((new Date(e.end_at) - new Date(e.start_at)) / 86400000)) : 0;
    return `<div class="item">
      <span class="swatch" style="background:${esc(c?.color)}"></span>
      <div class="grow"><div class="title">${esc(e.title)}</div>
        <div class="muted small">${esc(fmtDateTime(e.start_at))}${e.end_at ? ` – ${esc(fmtDateTime(e.end_at))} (${days} d)` : ""} · ${esc(c?.name)}</div>
        ${e.note ? `<div class="small">${esc(e.note)}</div>` : ""}</div>
      <button class="btn small" data-edit="${esc(e.id)}">Edit</button></div>`;
  }).join("") : `<div class="empty">No events yet. Mark trips, illness, alcohol, bad nights… they show up in every chart.</div>`;
  $$("[data-edit]", list).forEach((b) => (b.onclick = () => editEvent(b.dataset.edit)));
  view.append(list);

  const catCard = document.createElement("section");
  catCard.className = "card";
  catCard.style.marginTop = "12px";
  catCard.innerHTML = `<h3>Categories</h3><div class="list" style="margin:8px 0">${cats.map((c) => `<div class="item">
      <span class="swatch" style="background:${esc(c.color)}"></span><div class="grow">${esc(c.name)} ${c.archived ? '<span class="chip">archived</span>' : ""}</div>
      <button class="btn small" data-cat="${c.id}">Edit</button></div>`).join("")}</div>
    <button class="btn" data-newcat>New category</button>`;
  $$("[data-cat]", catCard).forEach((b) => (b.onclick = () => categoryDialog(cats.find((c) => c.id === Number(b.dataset.cat)))));
  $("[data-newcat]", catCard).onclick = () => categoryDialog();
  view.append(catCard);
}

async function viewSettings() {
  const [scales, tokens] = await Promise.all([api.scales(true), api.tokens()]);
  const demo = state.status?.demo;
  view.innerHTML = `<h2>Settings</h2>
    <section class="card"><h3>Wellbeing scales</h3>
      <p class="muted small">Scales are data, not code. Changing a scale's range creates a new version; old ratings keep their meaning.</p>
      <div class="list" style="margin:8px 0">${scales.map((s) => `<div class="item">
        <span class="swatch" style="background:${esc(s.color || "#888")}"></span>
        <div class="grow"><div class="title">${esc(s.name)} ${s.archived ? '<span class="chip">archived</span>' : ""}</div>
          <div class="muted small">${esc(round(s.min_value, 1))}${s.min_label ? ` = ${esc(s.min_label)}` : ""} … ${esc(round(s.max_value, 1))}${s.max_label ? ` = ${esc(s.max_label)}` : ""} · step ${esc(round(s.step, 2))} · v${s.version}</div></div>
        <button class="btn small" data-scale="${s.id}">Edit</button></div>`).join("") || `<p class="muted">No scales yet.</p>`}</div>
      <button class="btn" data-newscale>New scale</button></section>
    <section class="card" style="margin-top:12px"><h3>Device tokens</h3>
      <p class="muted small">For the future Android app and other clients: <code>Authorization: Bearer …</code>. See <a href="api/docs">API docs</a>.</p>
      <div class="list" style="margin:8px 0">${tokens.map((t) => `<div class="item"><div class="grow"><div class="title">${esc(t.name)} ${t.revoked ? '<span class="chip">revoked</span>' : ""}</div>
        <div class="muted small">${esc(t.prefix)}… · created ${esc(fmtDate(t.created_at))} · ${t.last_used_at ? `last used ${esc(ago(t.last_used_at))}` : "never used"}</div></div>
        ${t.revoked ? "" : `<button class="btn small danger" data-revoke="${t.id}">Revoke</button>`}</div>`).join("") || `<p class="muted small">No tokens.</p>`}</div>
      <button class="btn" data-newtoken ${demo ? "disabled" : ""}>New token</button></section>
    <section class="card" style="margin-top:12px"><h3>Session</h3>
      <div class="toolbar">${demo ? `<button class="btn" data-reset>Reset demo data</button>` : `<button class="btn" data-logout>Log out</button>`}
      <a class="btn" href="api/docs" style="text-decoration:none;display:inline-flex;align-items:center">API docs</a></div>
      <p class="muted small">pulse-mirror ${esc(state.status?.version)} · data ${esc(state.status?.data_range?.[0] || "–")} … ${esc(state.status?.data_range?.[1] || "–")}
      ${state.status?.backfill ? ` · backfill ${esc(state.status.backfill.status)} (back to ${esc(state.status.backfill.cursor_date)})` : ""}</p></section>`;
  $$("[data-scale]", view).forEach((b) => (b.onclick = () => scaleDialog(scales.find((s) => s.id === Number(b.dataset.scale)))));
  $("[data-newscale]", view).onclick = () => scaleDialog();
  $$("[data-revoke]", view).forEach((b) => (b.onclick = () => guard(async () => {
    if (confirm("Revoke this token? The device will lose access.")) { await api.revokeToken(Number(b.dataset.revoke)); route(); }
  })));
  $("[data-newtoken]", view).onclick = () => guard(async () => {
    const fd = await dialog("New device token", `<label class="field"><span>Device name</span><input type="text" name="name" required placeholder="Pixel phone"></label>`, { submit: "Create" });
    if (!fd) return;
    const t = await api.createToken(fd.get("name"));
    await dialog("Token created", `<p>Copy it now. It won't be shown again.</p><input type="text" readonly value="${esc(t.token)}" onclick="this.select()">`, { submit: null });
    route();
  });
  $("[data-logout]", view)?.addEventListener("click", () => guard(async () => { await api.logout(); renderLogin(); }));
  $("[data-reset]", view)?.addEventListener("click", () => { api.reset(); location.reload(); });
}

// --- dialogs ---------------------------------------------------------------------------------

function presetEvent(kind) {
  const now = new Date();
  if (kind === "night") {
    const start = new Date(now); start.setDate(start.getDate() - 1); start.setHours(22, 0, 0, 0);
    const end = new Date(now); end.setHours(7, 0, 0, 0);
    return { start_at: start.toISOString(), end_at: end.toISOString(), category_id: state.categories.find((c) => c.key === "poor_sleep")?.id };
  }
  if (kind === "span") {
    const start = new Date(now); start.setHours(0, 0, 0, 0);
    const end = new Date(start); end.setDate(end.getDate() + 2); end.setHours(23, 0, 0, 0);
    return { start_at: start.toISOString(), end_at: end.toISOString() };
  }
  return { start_at: now.toISOString(), end_at: null };
}

async function editEvent(id) {
  const all = await loadEvents();
  const e = all.find((x) => x.id === id);
  if (e) eventDialog(e);
}

async function eventDialog(e) {
  const isNew = !e.id;
  const cats = state.categories.filter((c) => !c.archived || c.id === e.category_id);
  const fd = await dialog(isNew ? "New event" : "Edit event", `
    <label class="field"><span>Title</span><input type="text" name="title" required maxlength="200" value="${esc(e.title || "")}" placeholder="e.g. Gravel tour, cold, late party"></label>
    <label class="field"><span>Category</span><select name="category_id">${cats.map((c) =>
      `<option value="${c.id}" ${c.id === e.category_id ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select></label>
    <div class="row">
      <label class="field"><span>Start</span><input type="datetime-local" name="start" required value="${esc(localInput(new Date(e.start_at)))}"></label>
      <label class="field"><span>End <span class="muted">(empty = point in time)</span></span><input type="datetime-local" name="end" value="${e.end_at ? esc(localInput(new Date(e.end_at))) : ""}"></label>
    </div>
    <label class="field"><span>Note</span><textarea name="note" maxlength="5000">${esc(e.note || "")}</textarea></label>`,
  { danger: isNew ? null : "Delete" });
  if (!fd) return;
  await guard(async () => {
    if (fd === "danger") {
      if (!confirm(`Delete "${e.title}"?`)) return;
      await api.deleteEvent(e.id); toast("Event deleted."); route(); return;
    }
    const body = {
      title: fd.get("title").trim(), category_id: Number(fd.get("category_id")),
      note: fd.get("note").trim() || null, start_at: fromInput(fd.get("start")),
    };
    const end = fromInput(fd.get("end"));
    if (isNew) await api.createEvent({ ...body, id: uuid(), end_at: end });
    else await api.updateEvent(e.id, end ? { ...body, end_at: end } : { ...body, clear_end: true });
    toast(isNew ? "Event saved." : "Event updated."); route();
  });
}

async function categoryDialog(c = {}) {
  const fd = await dialog(c.id ? "Edit category" : "New category", `
    <label class="field"><span>Name</span><input type="text" name="name" required maxlength="60" value="${esc(c.name || "")}"></label>
    <label class="field"><span>Color</span><input type="color" name="color" value="${esc(c.color || "#6c757d")}"></label>
    ${c.id ? `<label class="chip"><input type="checkbox" name="archived" ${c.archived ? "checked" : ""}> archived (hidden when adding events)</label>` : ""}`);
  if (!fd) return;
  await guard(async () => {
    if (c.id) await api.updateCategory(c.id, { name: fd.get("name"), color: fd.get("color"), archived: fd.has("archived") });
    else {
      const key = fd.get("name").toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 30) || "cat";
      await api.createCategory({ key: /^[a-z]/.test(key) ? key : `c_${key}`, name: fd.get("name"), color: fd.get("color") });
    }
    state.categories = await api.categories(); route();
  });
}

async function scaleDialog(s = {}) {
  const fd = await dialog(s.id ? "Edit scale" : "New scale", `
    <label class="field"><span>Name</span><input type="text" name="name" required maxlength="60" value="${esc(s.name || "")}" placeholder="Energy"></label>
    <label class="field"><span>Description</span><input type="text" name="description" maxlength="1000" value="${esc(s.description || "")}"></label>
    <div class="row">
      <label class="field"><span>Min</span><input type="number" step="any" name="min_value" required value="${esc(s.min_value ?? 1)}"></label>
      <label class="field"><span>Max</span><input type="number" step="any" name="max_value" required value="${esc(s.max_value ?? 10)}"></label>
      <label class="field"><span>Step</span><input type="number" step="any" min="0" name="step" required value="${esc(s.step ?? 1)}"></label>
    </div>
    <div class="row">
      <label class="field"><span>Label at min</span><input type="text" name="min_label" maxlength="60" value="${esc(s.min_label || "")}" placeholder="terrible"></label>
      <label class="field"><span>Label at max</span><input type="text" name="max_label" maxlength="60" value="${esc(s.max_label || "")}" placeholder="excellent"></label>
    </div>
    <label class="field"><span>Color</span><input type="color" name="color" value="${esc(s.color || "#257de0")}"></label>
    ${s.id ? `<label class="chip"><input type="checkbox" name="archived" ${s.archived ? "checked" : ""}> archived (hidden from the rating form, history kept)</label>
      <p class="muted small">Changing min, max or step creates version ${s.version + 1}. Existing ratings stay on version ${s.version}.</p>` : ""}`);
  if (!fd) return;
  await guard(async () => {
    const body = {
      name: fd.get("name"), description: fd.get("description") || null,
      min_value: Number(fd.get("min_value")), max_value: Number(fd.get("max_value")), step: Number(fd.get("step")),
      min_label: fd.get("min_label") || null, max_label: fd.get("max_label") || null, color: fd.get("color"),
    };
    if (s.id) await api.updateScale(s.id, { ...body, archived: fd.has("archived") });
    else {
      const key = body.name.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 30) || "scale";
      await api.createScale({ ...body, key: /^[a-z]/.test(key) ? key : `s_${key}` });
    }
    toast("Scale saved."); route();
  });
}

async function ratingDialog() {
  const scales = await guard(() => api.scales());
  if (!scales) return;
  if (!scales.length) { toast("Create a scale in Settings first."); location.hash = "#settings"; return; }
  const fd = await dialog("How are you?", `
    ${scales.map((s) => {
      const mid = Math.round(((s.min_value + s.max_value) / 2) / s.step) * s.step;
      return `<div class="slider"><div class="top"><b>${esc(s.name)}</b><output id="out-${s.id}">${esc(round(mid, 1))}</output></div>
        <input type="range" name="scale-${s.id}" min="${s.min_value}" max="${s.max_value}" step="${s.step}" value="${mid}"
          oninput="document.getElementById('out-${s.id}').textContent=this.value">
        <div class="ends"><span>${esc(s.min_label || s.min_value)}</span><span>${esc(s.max_label || s.max_value)}</span></div></div>`;
    }).join("")}
    <label class="field"><span>Note</span><textarea name="note" maxlength="2000" placeholder="optional"></textarea></label>
    <label class="field"><span>When</span><input type="datetime-local" name="when" value="${esc(localInput(new Date()))}"></label>`);
  if (!fd) return;
  await guard(async () => {
    const recorded_at = fromInput(fd.get("when")) || new Date().toISOString();
    const note = fd.get("note").trim() || null;
    for (const s of scales) {
      await api.putRating(uuid(), { scale_id: s.id, value: Number(fd.get(`scale-${s.id}`)), note, recorded_at });
    }
    toast("Thanks, saved."); route();
  });
}

async function editRating(r, byScale) {
  const s = byScale.get(r.scale_id);
  const fd = await dialog(`Edit rating: ${s?.name}`, `
    <label class="field"><span>Value</span><input type="number" name="value" step="${s?.step ?? "any"}" min="${s?.min_value}" max="${s?.max_value}" value="${esc(r.value)}" required></label>
    <label class="field"><span>Note</span><textarea name="note" maxlength="2000">${esc(r.note || "")}</textarea></label>
    <label class="field"><span>When</span><input type="datetime-local" name="when" value="${esc(localInput(new Date(r.recorded_at)))}"></label>`,
  { danger: "Delete" });
  if (!fd) return;
  await guard(async () => {
    if (fd === "danger") { await api.deleteRating(r.id); toast("Rating deleted."); route(); return; }
    await api.putRating(r.id, { scale_id: r.scale_id, value: Number(fd.get("value")), note: fd.get("note").trim() || null, recorded_at: fromInput(fd.get("when")) });
    toast("Rating updated."); route();
  });
}

// --- login & routing ------------------------------------------------------------------------------

function renderLogin() {
  disposeAll();
  $("#tabs").hidden = true;
  view.innerHTML = `<form class="card login" id="login-form"><h2 style="margin-top:0">Log in</h2>
    <label class="field"><span>Dashboard password</span><input type="password" name="password" required autocomplete="current-password" autofocus></label>
    <button class="btn primary" type="submit" style="width:100%">Log in</button>
    <p class="muted small">Set it on the server with <code>pulse-mirror auth set-password</code>.</p></form>`;
  $("#login-form").onsubmit = async (ev) => {
    ev.preventDefault();
    try {
      await api.login(new FormData(ev.target).get("password"));
      await start();
    } catch (e) { toast(e.message); }
  };
}

const VIEWS = { today: viewToday, trends: viewTrends, longterm: viewLongterm, activities: viewActivities,
  wellbeing: viewWellbeing, events: viewEvents, settings: viewSettings };

async function route() {
  const name = (location.hash || "#today").slice(1);
  const fn = VIEWS[name] || viewToday;
  $$("#tabs a").forEach((a) => a.setAttribute("aria-current", a.getAttribute("href") === `#${name}` ? "page" : "false"));
  disposeAll();
  view.innerHTML = `<div class="empty">Loading…</div>`;
  await guard(fn);
}

async function start() {
  await api.me();
  $("#tabs").hidden = false;
  [state.metrics, state.categories] = await Promise.all([api.metrics(), api.categories()]);
  await refreshStatus();
  await route();
}

window.addEventListener("hashchange", route);
$("#refresh-btn").onclick = onRefresh;
$("#sync-pill").onclick = () => refreshStatus();
const menu = $("#add-menu");
$("#add-btn").onclick = (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; };
document.addEventListener("click", () => (menu.hidden = true));
$$("[data-add]", menu).forEach((b) => (b.onclick = () => {
  menu.hidden = true;
  if (b.dataset.add === "rating") ratingDialog(); else eventDialog(presetEvent(b.dataset.add));
}));
setInterval(() => { if (!document.hidden && !$("#tabs").hidden) refreshStatus().catch(() => {}); }, 60000);

window.addEventListener("DOMContentLoaded", () => guard(start));
