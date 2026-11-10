import { useEffect, useState } from "react";
import { api } from "../api.js";
import { navigate } from "../App.jsx";

// Watchlists (plan §6.7): manage saved searches, sync-now, auto-download
// toggle, unread badge.
export default function Watchlists() {
  const [items, setItems] = useState([]);
  const [name, setName] = useState("");
  const [query, setQuery] = useState("");
  const [autoDl, setAutoDl] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const r = await api.get("/api/watchlists");
      setItems(r.items || []);
    } catch (e) {
      setError(e.message);
    }
  };
  useEffect(() => {
    load();
  }, []);

  const create = async () => {
    if (!name.trim() || !query.trim()) return;
    try {
      await api.post("/api/watchlists", { name: name.trim(), query: query.trim(), auto_download: autoDl });
      setName("");
      setQuery("");
      load();
    } catch (e) {
      setError(e.message);
    }
  };

  const syncNow = async (wl) => {
    setBusy(`sync:${wl.id}`);
    try {
      const r = await api.post(`/api/watchlists/${wl.id}/sync-now`);
      const msg =
        "error" in r ? `error: ${r.error}` : r.skipped ? "not due yet" : `${r.count} new galleries`;
      alert(`${wl.name}: ${msg}`);
      load();
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy("");
    }
  };

  const toggleAutoDl = async (wl) => {
    await api.patch(`/api/watchlists/${wl.id}`, { auto_download: !wl.auto_download });
    load();
  };

  const toggleEnabled = async (wl) => {
    await api.patch(`/api/watchlists/${wl.id}`, { enabled: !wl.enabled });
    load();
  };

  const del = async (wl) => {
    if (!confirm(`delete watchlist "${wl.name}"?`)) return;
    await api.del(`/api/watchlists/${wl.id}`);
    load();
  };

  return (
    <div className="p-6">
      <h1 className="mb-4 text-lg font-semibold">Watchlists</h1>
      <p className="mb-4 max-w-2xl text-xs text-slate-500">
        Saved searches run automatically in the background (interval configurable in
        Settings, default 6h). New results are added to your catalogue — and downloaded
        too, if auto-download is on. The unread badge counts new arrivals.
      </p>

      <div className="card mb-6 flex flex-wrap items-center gap-2 p-3">
        <input
          className="w-44"
          placeholder="name…"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <input
          className="min-w-64 flex-1"
          placeholder='query — e.g. artist:somename language:english'
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <label className="flex items-center gap-1.5 text-xs text-slate-400">
          <input type="checkbox" checked={autoDl} onChange={(e) => setAutoDl(e.target.checked)} />
          auto-download
        </label>
        <button className="btn btn-primary" onClick={create}>
          create
        </button>
      </div>

      {error && <div className="mb-3 text-sm text-rose-400">{error}</div>}

      <div className="space-y-2">
        {items.map((wl) => (
          <div key={wl.id} className="card flex flex-wrap items-center gap-3 p-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="font-medium">{wl.name}</span>
                {wl.unread_count > 0 && (
                  <span className="rounded-full bg-rose-600/80 px-2 py-0.5 text-[10px] font-bold text-white">
                    {wl.unread_count} new
                  </span>
                )}
                {!wl.enabled && <span className="text-xs text-slate-500">(paused)</span>}
              </div>
              <div className="mt-0.5 truncate text-xs text-slate-500">
                <code>{wl.query}</code>
                {wl.last_sync && ` · last sync ${wl.last_sync.replace("T", " ").slice(0, 16)}`}
                {` · cursor #${wl.last_seen_id}`}
              </div>
            </div>
            <button
              className="btn btn-primary text-xs"
              disabled={busy === `sync:${wl.id}`}
              onClick={() => syncNow(wl)}
            >
              {busy === `sync:${wl.id}` ? "syncing…" : "sync now"}
            </button>
            <button
              className={`btn btn-ghost text-xs ${wl.auto_download ? "text-emerald-300" : ""}`}
              onClick={() => toggleAutoDl(wl)}
              title="automatically download new results"
            >
              {wl.auto_download ? "⬇ auto-dl on" : "auto-dl off"}
            </button>
            <button className="btn btn-ghost text-xs" onClick={() => toggleEnabled(wl)}>
              {wl.enabled ? "pause" : "resume"}
            </button>
            <button
              className="btn btn-ghost text-xs"
              onClick={async () => {
                await api.post(`/api/watchlists/${wl.id}/seen`);
                load();
              }}
              title="clear unread badge"
            >
              mark seen
            </button>
            <button className="btn btn-ghost text-xs" onClick={() => navigate(`/browse?q=${encodeURIComponent(wl.query)}`)}>
              view query
            </button>
            <button className="btn btn-danger text-xs" onClick={() => del(wl)}>
              delete
            </button>
          </div>
        ))}
        {!items.length && (
          <div className="text-sm text-slate-500">
            no watchlists — create one above to track an artist or tag automatically
          </div>
        )}
      </div>
    </div>
  );
}
