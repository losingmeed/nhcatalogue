import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";

// Downloads manager (plan §6.8): queue with per-item progress bars,
// pause/resume/retry, open-folder info.
export default function Downloads() {
  const [items, setItems] = useState(null);
  const [error, setError] = useState("");
  const timer = useRef(null);

  const load = useCallback(async () => {
    try {
      const r = await api.get("/api/downloads");
      setItems(r.items || []);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    load();
    timer.current = setInterval(load, 1500);
    return () => clearInterval(timer.current);
  }, [load]);

  const act = async (gid, action) => {
    try {
      await api.post(`/api/downloads/${gid}`, { action });
    } catch (e) {
      alert(e.message);
    }
    load();
  };

  const statusColor = {
    queued: "text-slate-400",
    running: "text-sky-300",
    paused: "text-amber-300",
    done: "text-emerald-300",
    error: "text-rose-400",
    canceled: "text-slate-500",
  };

  return (
    <div className="p-6">
      <h1 className="mb-4 text-lg font-semibold">Downloads</h1>
      {error && <div className="mb-3 text-sm text-rose-400">{error}</div>}
      {!items && <div className="text-slate-500">loading…</div>}
      {items && !items.length && (
        <div className="text-sm text-slate-500">
          queue empty — download from a gallery page or the library bulk actions
        </div>
      )}
      <div className="space-y-2">
        {items?.map((q) => (
          <div key={q.id} className="card p-3">
            <div className="flex flex-wrap items-center gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-sm">
                  <span className="truncate">{q.title_pretty || q.title_en || `#${q.gallery_id}`}</span>
                  <span className={`text-xs font-semibold ${statusColor[q.status] || ""}`}>
                    {q.status}
                  </span>
                </div>
                <div className="mt-1 h-1.5 w-full overflow-hidden rounded bg-[#222734]">
                  <div
                    className={`h-full rounded ${
                      q.status === "error"
                        ? "bg-rose-600"
                        : q.status === "done"
                          ? "bg-emerald-500"
                          : "bg-sky-500"
                    }`}
                    style={{ width: `${Math.min(100, q.progress || (q.pages_total ? (q.pages_done / q.pages_total) * 100 : 0))}%` }}
                  />
                </div>
                <div className="mt-1 flex flex-wrap gap-x-3 text-[10px] text-slate-500">
                  <span>
                    {q.pages_done}/{q.pages_total} pages
                  </span>
                  {q.retries > 0 && <span>{q.retries} retries</span>}
                  {q.error && <span className="text-rose-400/80">{q.error}</span>}
                  {q.folder_path && (
                    <span className="truncate" title={q.folder_path}>
                      {q.folder_path}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex flex-wrap gap-1">
                {(q.status === "running" || q.status === "queued") && (
                  <>
                    <button className="btn btn-ghost text-xs" onClick={() => act(q.gallery_id, "pause")}>
                      ⏸ pause
                    </button>
                    <button className="btn btn-ghost text-xs" onClick={() => act(q.gallery_id, "cancel")}>
                      ✕ cancel
                    </button>
                  </>
                )}
                {q.status === "paused" && (
                  <button className="btn btn-primary text-xs" onClick={() => act(q.gallery_id, "resume")}>
                    ▶ resume
                  </button>
                )}
                {(q.status === "error" || q.status === "canceled" || q.status === "done") && (
                  <button className="btn btn-ghost text-xs" onClick={() => act(q.gallery_id, "retry")}>
                    ↻ re-download
                  </button>
                )}
                <a className="btn btn-ghost text-xs" href={`#/gallery/${q.gallery_id}`}>
                  open →
                </a>
                {q.folder_path && (
                  <button
                    className="btn btn-ghost text-xs"
                    title={q.folder_path}
                    onClick={() => navigator.clipboard?.writeText(q.folder_path)}
                  >
                    📂 copy path
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
