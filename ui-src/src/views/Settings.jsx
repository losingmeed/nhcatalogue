import { useEffect, useState } from "react";
import { api } from "../api.js";

// Settings (plan §6.9): domains/mirrors editor, proxy + test + status,
// cf_clearance + UA paste, FlareSolverr URL, library root, folder template,
// rate limits, reader prefs.
function Row({ label, hint, children }) {
  return (
    <div className="flex flex-wrap items-center gap-3 py-2">
      <div className="w-52 shrink-0 text-sm text-slate-300">{label}</div>
      <div className="min-w-64 flex-1">{children}</div>
      {hint && <div className="w-full pl-52 text-[11px] text-slate-600">{hint}</div>}
    </div>
  );
}

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
        library_root: s.library_root,
        folder_template: s.folder_template,
        rate_limit_rps: +s.rate_limit_rps,
        rate_limit_jitter: +s.rate_limit_jitter,
        host_concurrency: +s.host_concurrency,
        download_workers: +s.download_workers,
        watchlist_interval_hours: +s.watchlist_interval_hours,
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
        <Row label="Download workers">
          <input
            type="number"
            min={1}
            max={10}
            className="w-20"
            value={s.download_workers}
            onChange={(e) => set("download_workers", e.target.value)}
          />
        </Row>
      </section>

      {/* ---------------------------------------------------------- politeness */}
      <section className="card mb-4 p-4">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
          politeness
        </h2>
        <Row label="Rate limit (req/sec)" hint="keep at 1 unless you know what you're doing">
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
            max={10}
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
