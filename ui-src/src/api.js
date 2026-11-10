// API client: token bootstrap + typed fetch wrappers.

const LS_TOKEN = "nhc_token";

export function bootstrapToken() {
  // desktop shell passes ?#token=... in the URL hash on first launch
  const m = window.location.hash.match(/[#&]token=([^&]+)/);
  if (m) {
    localStorage.setItem(LS_TOKEN, m[1]);
    window.history.replaceState(null, "", window.location.pathname);
    return m[1];
  }
  return localStorage.getItem(LS_TOKEN) || "";
}

export const token = bootstrapToken();

export class ApiError extends Error {
  constructor(status, message, body) {
    super(message || `HTTP ${status}`);
    this.status = status;
    this.body = body;
    this.blocked = status === 502 && /blocked|challenge/i.test(String(message));
  }
}

async function req(method, path, body, params) {
  let url = path;
  if (params) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length)) continue;
      if (Array.isArray(v)) v.forEach((x) => qs.append(k, x));
      else qs.append(k, v);
    }
    const s = qs.toString();
    if (s) url += (url.includes("?") ? "&" : "?") + s;
  }
  const r = await fetch(url, {
    method,
    headers: {
      "X-NHC-Token": token,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  if (!r.ok) {
    let msg = "";
    try {
      const j = await r.json();
      msg = j.detail || j.message || JSON.stringify(j);
    } catch {
      msg = r.statusText;
    }
    throw new ApiError(r.status, msg);
  }
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r;
}

export const api = {
  get: (p, params) => req("GET", p, undefined, params),
  post: (p, body, params) => req("POST", p, body, params),
  put: (p, body) => req("PUT", p, body),
  patch: (p, body) => req("PATCH", p, body),
  del: (p, params) => req("DELETE", p, undefined, params),
};

export const pageUrl = (gid, n) => `/api/reader/${gid}/page/${n}`;
