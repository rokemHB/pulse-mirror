// API client. "live" talks to /api/v1; "static" (GitHub Pages demo) serves the
// pre-built JSON snapshots and keeps your edits in localStorage.

export class AuthError extends Error {}
export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export function uuid() {
  // crypto.randomUUID needs a secure context; the LAN dashboard runs on plain http.
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

const qs = (params) => {
  const p = Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== null && v !== "");
  return p.length ? "?" + new URLSearchParams(p).toString() : "";
};

class LiveApi {
  mode = "live";

  async req(method, path, body) {
    const res = await fetch("api/v1" + path, {
      method,
      credentials: "same-origin",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401 && path !== "/auth/login") throw new AuthError("Not logged in");
    if (res.status === 204) return null;
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      let msg = data?.detail ?? res.statusText;
      if (Array.isArray(msg)) msg = msg.map((d) => `${d.loc?.slice(-1)[0]}: ${d.msg}`).join("; ");
      throw new ApiError(res.status, msg);
    }
    return data;
  }

  login(password) { return this.req("POST", "/auth/login", { password }); }
  logout() { return this.req("POST", "/auth/logout"); }
  me() { return this.req("GET", "/auth/me"); }
  status() { return this.req("GET", "/status"); }
  sync() { return this.req("POST", "/sync"); }
  metrics() { return this.req("GET", "/metrics"); }
  daily(keys, from, to) { return this.req("GET", "/metrics/daily" + qs({ keys: keys?.join(","), from, to })); }
  activities(from, to) { return this.req("GET", "/activities" + qs({ from, to, limit: 5000 })); }
  categories() { return this.req("GET", "/event-categories"); }
  createCategory(b) { return this.req("POST", "/event-categories", b); }
  updateCategory(id, b) { return this.req("PATCH", `/event-categories/${id}`, b); }
  events(from, to) { return this.req("GET", "/events" + qs({ from, to })); }
  createEvent(b) { return this.req("POST", "/events", b); }
  updateEvent(id, b) { return this.req("PATCH", `/events/${id}`, b); }
  deleteEvent(id) { return this.req("DELETE", `/events/${id}`); }
  scales(includeArchived) { return this.req("GET", "/scales" + qs({ include_archived: includeArchived || undefined })); }
  createScale(b) { return this.req("POST", "/scales", b); }
  updateScale(id, b) { return this.req("PATCH", `/scales/${id}`, b); }
  ratings(from, to) { return this.req("GET", "/ratings" + qs({ from, to })); }
  putRating(id, b) { return this.req("PUT", `/ratings/${id}`, b); }
  deleteRating(id) { return this.req("DELETE", `/ratings/${id}`); }
  tokens() { return this.req("GET", "/tokens"); }
  createToken(name) { return this.req("POST", "/tokens", { name }); }
  revokeToken(id) { return this.req("DELETE", `/tokens/${id}`); }
}

// --- static demo adapter ---------------------------------------------------------------

const STORE = "pulse-mirror-demo:";
const nowIso = () => new Date().toISOString().replace(/\.\d+Z$/, "Z");
const inRange = (d, from, to) => (!from || d >= from) && (!to || d <= to);

class StaticApi {
  mode = "static";

  constructor(base) { this.base = base; this.cache = {}; }

  async file(name) {
    if (!this.cache[name]) {
      let saved = null;
      try { saved = localStorage.getItem(STORE + name); } catch { /* storage blocked: start fresh */ }
      this.cache[name] = saved ? JSON.parse(saved)
        : await fetch(this.base + name + ".json").then((r) => r.json());
    }
    return this.cache[name];
  }
  save(name) { try { localStorage.setItem(STORE + name, JSON.stringify(this.cache[name])); } catch { /* keep in memory */ } }
  reset() {
    try { Object.keys(localStorage).filter((k) => k.startsWith(STORE)).forEach((k) => localStorage.removeItem(k)); } catch { /* nothing stored */ }
  }

  async login() { return { kind: "session" }; }
  async logout() {}
  async me() { return { kind: "session" }; }
  async status() { return this.file("status"); }
  async sync() { throw new ApiError(409, "This is the demo: refreshing from Garmin is disabled."); }
  async metrics() { return this.file("metrics"); }
  async daily(keys, from, to) {
    const all = await this.file("daily");
    return all.filter((s) => !keys || keys.includes(s.key))
      .map((s) => ({ ...s, points: s.points.filter(([d]) => inRange(d, from, to)) }));
  }
  async activities(from, to) {
    return (await this.file("activities")).filter((a) => inRange(a.local_date, from, to));
  }
  async categories() { return this.file("event-categories"); }
  async createCategory(b) {
    const cats = await this.file("event-categories");
    const c = { id: Math.max(0, ...cats.map((x) => x.id)) + 1, icon: null, sort_order: 50, archived: false, ...b };
    cats.push(c); this.save("event-categories"); return c;
  }
  async updateCategory(id, b) {
    const c = (await this.file("event-categories")).find((x) => x.id === id);
    Object.assign(c, b); this.save("event-categories"); return c;
  }
  async events(from, to) {
    const evs = await this.file("events");
    return evs.filter((e) => (!to || e.start_at.slice(0, 10) <= to)
      && (!from || (e.end_at || e.start_at).slice(0, 10) >= from))
      .sort((a, b) => a.start_at.localeCompare(b.start_at));
  }
  async createEvent(b) {
    const evs = await this.file("events");
    const e = { id: b.id || uuid(), note: null, end_at: null, ...b, source: "manual",
      created_at: nowIso(), updated_at: nowIso() };
    evs.push(e); this.save("events"); return e;
  }
  async updateEvent(id, b) {
    const e = (await this.file("events")).find((x) => x.id === id);
    const { clear_end, ...rest } = b;
    Object.assign(e, rest, { updated_at: nowIso() });
    if (clear_end) e.end_at = null;
    this.save("events"); return e;
  }
  async deleteEvent(id) {
    const evs = await this.file("events");
    this.cache.events = evs.filter((x) => x.id !== id); this.save("events");
  }
  async scales(includeArchived) {
    return (await this.file("scales")).filter((s) => includeArchived || !s.archived);
  }
  async createScale(b) {
    const scales = await this.file("scales");
    const id = Math.max(0, ...scales.map((x) => x.id)) + 1;
    const s = { id, version: 1, version_id: id * 1000 + 1, archived: false, description: null,
      min_label: null, max_label: null, color: null, step: 1, sort_order: 0, ...b };
    scales.push(s); this.save("scales"); return s;
  }
  async updateScale(id, b) {
    const s = (await this.file("scales")).find((x) => x.id === id);
    const meaning = ["min_value", "max_value", "step"].some((k) => k in b && b[k] !== s[k]);
    Object.assign(s, b);
    if (meaning) { s.version += 1; s.version_id = s.id * 1000 + s.version; }
    this.save("scales"); return s;
  }
  async ratings(from, to) {
    return (await this.file("ratings")).filter((r) => !r.deleted && inRange(r.recorded_at.slice(0, 10), from, to));
  }
  async putRating(id, b) {
    const ratings = await this.file("ratings");
    const scale = (await this.file("scales")).find((s) => s.id === b.scale_id);
    let r = ratings.find((x) => x.id === id);
    if (!r) {
      r = { id, scale_version_id: scale.version_id, source: "manual", created_at: nowIso(), deleted: false };
      ratings.push(r);
    }
    Object.assign(r, b, { updated_at: nowIso() }); this.save("ratings"); return r;
  }
  async deleteRating(id) {
    const r = (await this.file("ratings")).find((x) => x.id === id);
    if (r) { r.deleted = true; this.save("ratings"); }
  }
  async tokens() { return []; }
  async createToken() { throw new ApiError(409, "API tokens aren't available in the demo."); }
  async revokeToken() {}
}

const cfg = window.PULSE_CONFIG || { mode: "live" };
export const api = cfg.mode === "static" ? new StaticApi(cfg.dataUrl || "demo-data/") : new LiveApi();
