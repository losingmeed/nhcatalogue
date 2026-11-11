import { useEffect, useState } from "react";
import { api } from "../api.js";
import {
  THEME_GROUPS,
  THEME_PRESETS,
  applyTheme,
  normalizeHex,
  normalizeTheme,
  presetKeyFor,
} from "../theme.js";

// Settings (plan §6.9): domains/mirrors editor, proxy + test + status,
// cf_clearance + UA paste, FlareSolverr URL, library root, folder template,
// rate limits, reader prefs, full colour theme.
function Row({ label, hint, children }) {
  return (
    <div className="flex flex-wrap items-center gap-3 py-2">
      <div className="w-52 shrink-0 text-sm text-slate-300">{label}</div>
      <div className="min-w-64 flex-1">{children}</div>
      {hint && <div className="w-full pl-52 text-[11px] text-slate-600">{hint}</div>}
    </div>
  );
}

// One colour token: native picker + editable hex field. Invalid text reverts
// on blur so the live theme never receives a broken value.
function ColorField({ label, value, onChange }) {
  const [draft, setDraft] = useState(value);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  const commit = () => {
    const hex = normalizeHex(draft);
    if (hex) onChange(hex);
    else setDraft(value);
  };

  return (
    <label className="flex items-center gap-2 py-0.5 text-xs">
      <input
        type="color"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        title={label}
        aria-label={label}
      />
      <span className="min-w-0 flex-1 truncate text-slate-300">{label}</span>
      <input
        className="w-24 px-1.5 py-0.5 font-mono text-[11px] uppercase"
        value={draft}
        spellCheck={false}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") e.currentTarget.blur();
        }}
      />
    </label>
  );
}

// One-click speed presets. Fast is the default; Turbo is for short bursts.
const PRESETS = {
  polite: {
    rate_limit_rps: 1,
    rate_limit_jitter: 0.35,
    host_concurrency: 3,
    download_page_concurrency: 2,
    download_workers: 3,
  },
  fast: {
    rate_limit_rps: 4,
    rate_limit_jitter: 0.15,
    host_concurrency: 6,
    download_page_concurrency: 6,
    download_workers: 4,
  },
  turbo: {
    rate_limit_rps: 10,
    rate_limit_jitter: 0.05,
    host_concurrency: 10,
    download_page_concurrency: 12,
    download_workers: 6,
  },
};

export default function SettingsView({ settings, reload }) {
  const [s, setS] = useState(null);
  const [saving, setSaving] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (settings) setS({ ...settings });
  }, [settings]);

  if (!s) return <div className="p-6 text-slate-500">loading…</div>;

  const set = (k, v) => setS((prev) => ({ ...prev, [k]: v }));

  const activePreset =
    Object.entries(PRESETS).find(([, preset]) =>
      Object.entries(preset).every(([k, v]) => +s[k] === v)
    )?.[0] || "custom";
  const applyPreset = (name) => setS((prev) => ({ ...prev, ...PRESETS[name] }));

  // ---- theme (live preview; persisted with the rest of the settings) ------
  const theme = normalizeTheme(s.ui_prefs?.theme);
  const activeThemePreset = presetKeyFor(theme);

  const setTheme = (next) => {
    setS((prev) => ({
      ...prev,
      ui_prefs: { ...(prev.ui_prefs || {}), theme: next },
    }));
    applyTheme(next);
  };
  const setThemeColor = (key, value) =>
    setTheme(normalizeTheme({ ...theme, [key]: value }));
  const applyThemePreset = (name) => setTheme({ ...THEME_PRESETS[name].colors });

  const save = async (extra = {}) => {
    setSaving(true);
    setMessage("");
    try {
      const patch = {
        domains: s.domains,
        proxy: s.proxy || "",
        cf_clearance: s.cf_clearance || "",
        cf_user_agent: s.cf_user_agent || "",
        flaresolverr_url: s.flaresolverr_url || "",
        rule34_domain: s.rule34_domain || "",
        rule34_api_host: s.rule34_api_host || "",
        rule34_user_id: s.rule34_user_id || "",
        rule34_api_key: s.rule34_api_key || "",
        library_root: s.library_root,
        folder_template: s.folder_template,
        rate_limit_rps: +s.rate_limit_rps,
        rate_limit_jitter: +s.rate_limit_jitter,
        host_concurrency: +s.host_concurrency,
        download_workers: +s.download_workers,
        download_page_concurrency: +s.download_page_concurrency,
        page_cache_max_mb: +s.page_cache_max_mb,
        temp_page_cache_ttl_s: +s.temp_page_cache_ttl_s,
        watchlist_interval_hours: +s.watchlist_interval_hours,
        ui_prefs: s.ui_prefs || {},
        ...extra,
      };
      const r = await api.put("/api/settings", { patch });
      setMessage("saved ✓");
      setS({ ...r });
      reload();
    } catch (e) {
      setMessage(`save failed: ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTestResult({ running: true });
    try {
      const r = await api.post("/api/settings/test");
      setTestResult(r);
    } catch (e) {
      setTestResult({ ok: false, message: e.message });
    }
  };

  const moveDomain = (i, dir) => {
    const d = [...s.domains];
    const j = i + dir;
    if (j < 0 || j >= d.length) return;
    [d[i], d[j]] = [d[j], d[i]];
    set("domains", d);
  };

  return (
    <div className="mx-auto max-w-4xl p-6">
      <h1 className="mb-4 text-lg font-semibold">Settings</h1>
      {message && <div className="mb-3 text-sm text-indigo-300">{message}</div>}

      {/* ---------------------------------------------------------------- theme */}
      <section className="card mb-4 p-4">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
            theme
          </h2>
          <div className="flex flex-wrap items-center gap-1.5">
            {Object.entries(THEME_PRESETS).map(([key, preset]) => (
              <button
                key={key}
                className={`btn text-xs ${activeThemePreset === key ? "btn-primary" : "btn-ghost"}`}
                onClick={() => applyThemePreset(key)}
                title={`apply the ${preset.label} palette`}
              >
                <span
                  className="inline-block h-3 w-3 rounded-full border border-white/20"
                  style={{ background: preset.colors.accent }}
                />
                {preset.label}
              </button>
            ))}
            {activeThemePreset === "custom" && (
              <span className="text-xs text-slate-500">custom</span>
            )}
            <button
              className="btn btn-ghost text-xs"
              onClick={() => applyThemePreset("midnight")}
              title="restore the default colours"
            >
              reset
            </button>
          </div>
        </div>
        <div className="mb-3 text-[11px] text-slate-500">
          Every colour in the app is editable here. Changes preview live — press
          “save settings” to keep them.
        </div>

        {/* live sample using the same utility classes as the rest of the app */}
        <div className="mb-4 rounded-md border border-line-strong bg-panel-alt p-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-slate-200">Primary text</span>
            <span className="text-xs text-slate-400">secondary</span>
            <span className="text-xs text-slate-500">muted</span>
            <button className="btn btn-primary text-xs" type="button">
              primary
            </button>
            <button className="btn btn-ghost text-xs" type="button">
              ghost
            </button>
            <button className="btn btn-danger text-xs" type="button">
              danger
            </button>
            <span className="rounded-full border border-violet-700/50 bg-violet-600/25 px-2 py-0.5 text-xs text-violet-200">
              artist
            </span>
            <span className="rounded-full border border-teal-700/50 bg-teal-600/25 px-2 py-0.5 text-xs text-teal-200">
              character
            </span>
            <span className="rounded-full border border-pink-700/50 bg-pink-600/25 px-2 py-0.5 text-xs text-pink-200">
              group
            </span>
            <span className="rounded-full border border-sky-700/50 bg-sky-600/25 px-2 py-0.5 text-xs text-sky-200">
              language
            </span>
            <span className="rounded-full border border-emerald-700/50 bg-emerald-600/25 px-2 py-0.5 text-xs text-emerald-200">
              category
            </span>
            <span className="text-xs text-rose-300">error</span>
            <span className="text-xs text-amber-300">warning</span>
          </div>
        </div>

        <div className="grid gap-x-8 gap-y-3 sm:grid-cols-2 xl:grid-cols-3">
          {THEME_GROUPS.map((group) => (
            <div key={group.label}>
              <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                {group.label}
              </h3>
              {group.keys.map(([key, label]) => (
                <ColorField
                  key={key}
                  label={label}
                  value={theme[key]}
                  onChange={(value) => setThemeColor(key, value)}
                />
              ))}
            </div>
          ))}
        </div>
      </section>

      {/* ------------------------------------------------ access / restrictions */}
      <section className="card mb-4 p-4">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
          access & restrictions
        </h2>

        <Row
          label="Domain rotation"
          hint="ordered list — tried top to bottom; failures demote a domain automatically. Add same-API mirrors here."
        >
          <div className="space-y-1">
            {s.domains.map((d, i) => (
              <div key={i} className="flex items-center gap-1">
                <input
                  className="flex-1"
                  value={d}
                  onChange={(e) => {
                    const dd = [...s.domains];
                    dd[i] = e.target.value;
                    set("domains", dd);
                  }}
                />
                <button className="btn btn-ghost text-xs" onClick={() => moveDomain(i, -1)} title="move up">
                  ↑
                </button>
                <button className="btn btn-ghost text-xs" onClick={() => moveDomain(i, 1)} title="move down">
                  ↓
                </button>
                <button
                  className="btn btn-ghost text-xs text-rose-400"
                  onClick={() => set("domains", s.domains.filter((_, x) => x !== i))}
                >
                  ×
                </button>
              </div>
            ))}
            <button
              className="btn btn-ghost text-xs"
              onClick={() => set("domains", [...s.domains, ""])}
            >
              + add domain
            </button>
          </div>
          {s.domain_stats?.some((d) => d.demoted || d.consecutive_failures) && (
            <div className="mt-2 space-y-0.5 text-[11px] text-amber-300/70">
              {s.domain_stats
                .filter((d) => d.demoted || d.consecutive_failures)
                .map((d, i) => (
                  <div key={i}>
                    {d.domain}: {d.demoted ? "demoted" : `${d.consecutive_failures} failures`}
                    {d.last_failure_reason ? ` (${d.last_failure_reason})` : ""}
                  </div>
                ))}
            </div>
          )}
        </Row>

        <Row label="TLS transport">
          <div className="text-xs text-slate-400">
            {s.has_curl_cffi ? (
              <span className="text-emerald-300">curl_cffi — Chrome TLS/JA3 impersonation active</span>
            ) : (
              <span className="text-amber-300">
                httpx fallback (curl_cffi not installed — install with{" "}
                <code>pip install curl_cffi</code> for Cloudflare resistance)
              </span>
            )}
          </div>
        </Row>

        <Row
          label="cf_clearance cookie"
          hint="paste the cookie value AND the exact User-Agent string it was issued for — Cloudflare binds the clearance to UA + IP."
        >
          <input
            className="mb-1 w-full"
            placeholder="cf_clearance value…"
            value={s.cf_clearance || ""}
            onChange={(e) => set("cf_clearance", e.target.value)}
          />
          <input
            className="w-full"
            placeholder="matching User-Agent, e.g. Mozilla/5.0 … Chrome/…"
            value={s.cf_user_agent || ""}
            onChange={(e) => set("cf_user_agent", e.target.value)}
          />
        </Row>

        <Row
          label="FlareSolverr URL"
          hint="optional local CF-solving proxy (e.g. http://127.0.0.1:8191). Used automatically when challenges are detected."
        >
          <input
            placeholder="http://127.0.0.1:8191"
            value={s.flaresolverr_url || ""}
            onChange={(e) => set("flaresolverr_url", e.target.value)}
          />
        </Row>

        <Row
          label="Rule 34 credentials"
          hint="rule34's API needs an account id and key — get them from rule34 → account → options. Stored like any other setting."
        >
          <input
            className="mb-1 w-full"
            placeholder="rule34 site host (default rule34.xxx)"
            value={s.rule34_domain || ""}
            onChange={(e) => set("rule34_domain", e.target.value)}
          />
          <input
            className="mb-1 w-full"
            placeholder="rule34 API host (default api.rule34.xxx)"
            value={s.rule34_api_host || ""}
            onChange={(e) => set("rule34_api_host", e.target.value)}
          />
          <input
            className="mb-1 w-full"
            placeholder="rule34 user id…"
            value={s.rule34_user_id || ""}
            onChange={(e) => set("rule34_user_id", e.target.value)}
          />
          <input
            className="w-full"
            placeholder="rule34 api key…"
            type="password"
            value={s.rule34_api_key || ""}
            onChange={(e) => set("rule34_api_key", e.target.value)}
          />
        </Row>

        <Row
          label="Proxy (VPN)"
          hint="SOCKS5 or HTTP proxy for all nhentai hosts — the answer to country-level blocks."
        >
          <input
            placeholder="socks5://127.0.0.1:1080"
            value={s.proxy || ""}
            onChange={(e) => set("proxy", e.target.value)}
          />
        </Row>

        <Row label="Connection test">
          <div className="flex items-center gap-2">
            <button className="btn btn-primary" onClick={test}>
              test connection
            </button>
            {testResult?.running && <span className="text-xs text-slate-400">testing…</span>}
            {testResult && !testResult.running && (
              <div className={`text-xs ${testResult.ok ? "text-emerald-300" : "text-rose-400"}`}>
                {testResult.ok ? "✓" : "✗"} {testResult.message}
                {testResult.transport && ` (transport: ${testResult.transport})`}
              </div>
            )}
          </div>
        </Row>
      </section>

      {/* ------------------------------------------------------------- library */}
      <section className="card mb-4 p-4">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
          library
        </h2>
        <Row label="Library root">
          <input
            className="w-full"
            value={s.library_root || ""}
            onChange={(e) => set("library_root", e.target.value)}
          />
        </Row>
        <Row
          label="Folder template"
          hint="vars: {artist} {parody} {character} {language} {id} {title} — default {artist}/{id} - {title}"
        >
          <input
            className="w-full"
            value={s.folder_template || ""}
            onChange={(e) => set("folder_template", e.target.value)}
          />
          <div className="mt-1 text-[11px] text-slate-600">
            → {s.library_root}/{s.folder_template}
          </div>
        </Row>
        <Row label="Download workers" hint="galleries downloaded in parallel">
          <input
            type="number"
            min={1}
            max={10}
            className="w-20"
            value={s.download_workers}
            onChange={(e) => set("download_workers", e.target.value)}
          />
        </Row>
        <Row label="Page concurrency" hint="parallel page fetches inside one gallery">
          <input
            type="number"
            min={1}
            max={16}
            className="w-20"
            value={s.download_page_concurrency}
            onChange={(e) => set("download_page_concurrency", e.target.value)}
          />
        </Row>
        <Row
          label="Collection cache (MB)"
          hint="lazy page cache for galleries on a shelf — 0 disables it. Pages are cached as you read them and purged when a gallery leaves its last collection."
        >
          <input
            type="number"
            min={0}
            step={256}
            className="w-24"
            value={s.page_cache_max_mb}
            onChange={(e) => set("page_cache_max_mb", e.target.value)}
          />
        </Row>
        <Row
          label="Preview cache (seconds)"
          hint="how long pages for previewed comics stay cached before expiring — saving the comic (collection/download) promotes them to permanent storage."
        >
          <input
            type="number"
            min={1}
            step={5}
            className="w-24"
            value={s.temp_page_cache_ttl_s}
            onChange={(e) => set("temp_page_cache_ttl_s", e.target.value)}
          />
        </Row>
      </section>

      {/* ---------------------------------------------------------- politeness */}
      <section className="card mb-4 p-4">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
          speed &amp; politeness
        </h2>
        <Row
          label="Preset"
          hint="Fast is the default (4 req/s per CDN host, ~16 req/s aggregate). Turbo is faster but more likely to hit 429/Cloudflare."
        >
          <div className="flex flex-wrap items-center gap-2">
            {Object.keys(PRESETS).map((name) => (
              <button
                key={name}
                className={`btn text-xs ${activePreset === name ? "btn-primary" : "btn-ghost"}`}
                onClick={() => applyPreset(name)}
              >
                {name}
              </button>
            ))}
            <span className="text-xs text-slate-500">
              {activePreset === "custom" ? "custom" : `active: ${activePreset}`}
            </span>
          </div>
        </Row>
        <Row label="Rate limit (req/sec)" hint="per CDN host — pages rotate across i1–i4">
          <input
            type="number"
            step="0.5"
            min="0.1"
            max="10"
            className="w-20"
            value={s.rate_limit_rps}
            onChange={(e) => set("rate_limit_rps", e.target.value)}
          />
        </Row>
        <Row label="Jitter (fraction)">
          <input
            type="number"
            step="0.05"
            min="0"
            max="1"
            className="w-20"
            value={s.rate_limit_jitter}
            onChange={(e) => set("rate_limit_jitter", e.target.value)}
          />
        </Row>
        <Row label="Per-host concurrency" hint="requests in flight per host">
          <input
            type="number"
            min={1}
            max={16}
            className="w-20"
            value={s.host_concurrency}
            onChange={(e) => set("host_concurrency", e.target.value)}
          />
        </Row>
        <Row label="Watchlist interval (h)" hint="minimum 1 hour — watchlists are rate-limited">
          <input
            type="number"
            step="1"
            min="1"
            className="w-20"
            value={s.watchlist_interval_hours}
            onChange={(e) => set("watchlist_interval_hours", e.target.value)}
          />
        </Row>
      </section>

      <div className="flex gap-2">
        <button className="btn btn-primary" disabled={saving} onClick={() => save()}>
          {saving ? "saving…" : "save settings"}
        </button>
      </div>
    </div>
  );
}
