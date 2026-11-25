// ECharts helpers: one y-axis per chart, events as bands/markers in every chart,
// crosshair tooltip listing every series plus the events on that day.

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
export const seriesColor = (slot) => css(`--series-${slot}`);

// Fixed metric -> categorical slot. Color follows the entity, never its rank.
export const METRIC_SLOT = {
  resting_hr: 8, hrv_overnight_avg: 1, hrv_weekly_avg: 7, sleep_duration: 7, sleep_score: 3,
  body_battery_max: 6, body_battery_min: 3, stress_avg: 2, vo2max_cycling: 1, vo2max_running: 2,
  training_load_acute: 2, training_load_chronic: 1, steps: 3, sleep_deep: 7, sleep_rem: 5,
  sleep_light: 1, sleep_awake: 4, ride_hours: 3,
};

/** 'YYYY-MM-DD' -> local-midnight timestamp (what the time axis uses). */
export const dayTs = (d) => { const [y, m, dd] = d.split("-").map(Number); return new Date(y, m - 1, dd).getTime(); };
export const isoDay = (t) => {
  const d = new Date(t);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const fmtDay = (t) => new Date(t).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });

const charts = new Set();
window.addEventListener("resize", () => charts.forEach((c) => c.resize()));
export function disposeAll() { charts.forEach((c) => c.dispose()); charts.clear(); }

/**
 * @param el container
 * @param opts {series:[{name, slot, points:[[ts, v]], type:'line'|'bar', dashed?, area?}],
 *              unit, format(v), events, categories, onEventClick, group, zoom, yMin}
 */
export function timeChart(el, opts) {
  const chart = echarts.init(el, null, { renderer: "canvas" });
  charts.add(chart);
  const text2 = css("--text-secondary"), grid = css("--grid"), surface = css("--surface-1");
  const format = opts.format || ((v) => (Math.round(v * 10) / 10).toLocaleString());
  const catById = new Map((opts.categories || []).map((c) => [c.id, c]));
  const events = (opts.events || []).map((e) => ({
    ...e,
    t0: new Date(e.start_at).getTime(),
    t1: e.end_at ? new Date(e.end_at).getTime() : null,
    cat: catById.get(e.category_id),
  }));

  const series = opts.series.map((s) => ({
    name: s.name,
    type: s.type || "line",
    data: s.points,
    showSymbol: s.showSymbol ?? false,
    symbolSize: 7,
    connectNulls: false,
    barMaxWidth: 14,
    lineStyle: { width: 2, type: s.dashed ? "dashed" : "solid" },
    itemStyle: { color: seriesColor(s.slot), borderRadius: s.type === "bar" ? [4, 4, 0, 0] : 0 },
    areaStyle: s.area ? { opacity: 0.08 } : undefined,
    emphasis: { focus: "none" },
    z: 3,
  }));

  // Events ride on an invisible helper series so they appear in every chart.
  const spans = events.filter((e) => e.t1 !== null);
  const points = events.filter((e) => e.t1 === null);
  series.push({
    name: "__events",
    type: "line",
    data: [],
    silent: false,
    markArea: {
      silent: false,
      data: spans.map((e) => [
        { xAxis: e.t0, name: e.title, itemStyle: { color: e.cat?.color || "#888", opacity: 0.14 }, eventId: e.id },
        { xAxis: e.t1 },
      ]),
      label: { show: false },
      emphasis: { itemStyle: { opacity: 0.26 }, label: { show: false } },
    },
    markLine: {
      symbol: ["none", "circle"],
      symbolSize: 8,
      silent: false,
      label: { show: false },
      data: points.map((e) => ({
        xAxis: e.t0, name: e.title, eventId: e.id,
        lineStyle: { color: e.cat?.color || "#888", type: "dashed", width: 1.5 },
        itemStyle: { color: e.cat?.color || "#888" },
      })),
      emphasis: { lineStyle: { width: 3 }, label: { show: false } },
    },
  });

  const eventsOn = (t) => {
    const end = t + 86400000;
    return events.filter((e) => e.t0 < end && (e.t1 ?? e.t0) >= t);
  };

  chart.setOption({
    animation: false,
    grid: { left: 8, right: 16, top: 16, bottom: opts.zoom ? 56 : 24, containLabel: true },
    xAxis: {
      type: "time",
      axisLine: { lineStyle: { color: grid } },
      axisLabel: { color: text2, hideOverlap: true },
      splitLine: { show: false },
      min: opts.min, max: opts.max,
    },
    yAxis: {
      type: "value",
      scale: opts.yMin === undefined,
      min: opts.yMin,
      max: opts.yMax,
      axisLabel: { color: text2, formatter: (v) => format(v) },
      splitLine: { lineStyle: { color: grid } },
    },
    tooltip: {
      trigger: "axis",
      confine: true,
      backgroundColor: surface,
      borderColor: grid,
      textStyle: { color: css("--text-primary") },
      axisPointer: { type: "line", lineStyle: { color: text2, width: 1 } },
      formatter: (params) => {
        const list = Array.isArray(params) ? params : [params];
        const t = list[0]?.axisValue ?? list[0]?.value?.[0];
        const rows = list.filter((p) => p.seriesName !== "__events" && p.value?.[1] != null).map((p) =>
          `<div style="display:flex;gap:8px;align-items:center"><span style="width:12px;height:3px;border-radius:2px;background:${p.color}"></span>` +
          `<b>${esc(opts.formatTooltip ? opts.formatTooltip(p.value, p.seriesName) : format(p.value[1]))}</b>` +
          `<span style="opacity:.75">${esc(p.seriesName)}</span></div>`);
        const evs = t ? eventsOn(dayTs(isoDay(t))) : [];
        const evRows = evs.map((e) =>
          `<div style="display:flex;gap:6px;align-items:center;margin-top:2px"><span style="width:8px;height:8px;border-radius:2px;background:${e.cat?.color || "#888"}"></span>${esc(e.title)}</div>`);
        return `<div style="font-size:12px;opacity:.75;margin-bottom:4px">${t ? esc(fmtDay(t)) : ""}</div>` +
          rows.join("") + (evRows.length ? `<div style="margin-top:6px;border-top:1px solid ${grid};padding-top:4px">${evRows.join("")}</div>` : "");
      },
    },
    dataZoom: opts.zoom
      ? [{ type: "inside", filterMode: "none" },
         { type: "slider", height: 22, bottom: 8, filterMode: "none", borderColor: grid,
           textStyle: { color: text2 }, brushSelect: false }]
      : [{ type: "inside", filterMode: "none" }],
    series,
  });

  chart.on("click", (p) => {
    const id = p.data?.eventId ?? (Array.isArray(p.data) ? p.data[0]?.eventId : undefined);
    if (id && opts.onEventClick) opts.onEventClick(id);
  });
  if (opts.group) { chart.group = opts.group; echarts.connect(opts.group); }
  return chart;
}

/** Simple monthly/weekly bar chart for category axes (e.g. training volume). */
export function barChart(el, { labels, values, slot, unit, format }) {
  const chart = echarts.init(el);
  charts.add(chart);
  const text2 = css("--text-secondary"), grid = css("--grid");
  const f = format || ((v) => (Math.round(v * 10) / 10).toLocaleString());
  chart.setOption({
    animation: false,
    grid: { left: 8, right: 12, top: 16, bottom: 24, containLabel: true },
    xAxis: { type: "category", data: labels, axisLabel: { color: text2, hideOverlap: true }, axisLine: { lineStyle: { color: grid } } },
    yAxis: { type: "value", name: unit, nameTextStyle: { color: text2 }, axisLabel: { color: text2, formatter: f }, splitLine: { lineStyle: { color: grid } } },
    tooltip: { trigger: "item", confine: true, backgroundColor: css("--surface-1"), borderColor: grid,
      textStyle: { color: css("--text-primary") },
      formatter: (p) => `<b>${esc(f(p.value))}</b> ${esc(unit || "")}<br><span style="opacity:.75">${esc(p.name)}</span>` },
    series: [{ type: "bar", data: values, barMaxWidth: 18, itemStyle: { color: seriesColor(slot), borderRadius: [4, 4, 0, 0] } }],
  });
  return chart;
}
