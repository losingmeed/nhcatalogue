import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";

// Downloads manager (plan §6.8): queue with per-item progress bars,
// pause/resume/retry, open-folder info.
const fmtEta = (seconds) => {
  if (seconds == null || !isFinite(seconds) || seconds <= 0) return "";
  if (seconds < 60) return `${Math.ceil(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
};
const fmtRate = (n) => (n >= 100 ? String(Math.round(n)) : n.toFixed(1));
const fmtMB = (bytesPerSecond) => (bytesPerSecond / 1048576).toFixed(1);

export default function Downloads() {
  const [items, setItems] = useState(null);
  const [speeds, setSpeeds] = useState({});
  const [error, setError] = useState("");
  const timer = useRef(null);
  const samples = useRef({});
  const draggedIndex = useRef(null);

  const load = useCallback(async () => {
    try {
      const r = await api.get("/api/downloads");
      const next = r.items || [];
      const now = Date.now();
      const measured = {};
      for (const q of next) {
        const prev = samples.current[q.id];
        if (prev && now > prev.t) {
          const dt = (now - prev.t) / 1000;
          const pps = Math.max(0, (q.pages_done - prev.pages) / dt);
          const bps = Math.max(0, ((q.bytes_done || 0) - prev.bytes) / dt);
          measured[q.id] = {
            pps,
            bps,
            eta:
              pps > 0 && q.pages_total > q.pages_done
                ? (q.pages_total - q.pages_done) / pps
                : null,
          };
        }
        samples.current[q.id] = {
          t: now,
          pages: q.pages_done,
          bytes: q.bytes_done || 0,
        };
      }
      setItems(next);
      setSpeeds(measured);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    load();
    timer.current = setInterval(load, 1000);
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

  // Persist a new queue order (gallery ids) and refresh from the server.
  const reorder = async (nextIds) => {
    try {
      await api.post("/api/downloads/reorder", { order: nextIds });
    } catch (e) {
      alert(e.message);
    }
    load();
  };

  const moveUp = (index) => {
    if (index <= 0 || !items) return;
    const next = [...items];
    [next[index - 1], next[index]] = [next[index], next[index - 1]];
    setItems(next);
    reorder(next.map((q) => q.gallery_id));
  };

  const moveDown = (index) => {
    if (index < 0 || index >= (items?.length || 0) - 1 || !items) return;
    const next = [...items];
    [next[index], next[index + 1]] = [next[index + 1], next[index]];
    setItems(next);
    reorder(next.map((q) => q.gallery_id));
  };

  const onDragStart = (e, index) => {
    draggedIndex.current = index;
    e.dataTransfer.effectAllowed = "move";
  };

  const onDragOver = (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  };

  const onDrop = (e, targetIndex) => {
    const sourceIndex = draggedIndex.current;
    if (sourceIndex === null || sourceIndex === targetIndex || !items) return;
    draggedIndex.current = null;
    const next = [...items];
    const [moved] = next.splice(sourceIndex, 1);
    next.splice(targetIndex, 0, moved);
    setItems(next);
    reorder(next.map((q) => q.gallery_id));
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
        {items?.map((q, index) => (
          <div
            key={q.id}
            className="card p-3"
            draggable={true}
            onDragStart={(e) => onDragStart(e, index)}
            onDragOver={onDragOver}
            onDrop={(e) => onDrop(e, index)}
          >
            <div className="flex flex-wrap items-center gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-sm">
                  <span className="truncate">{q.title_pretty || q.title_en || `#${q.gallery_id}`}</span>
                  <span className={`text-xs font-semibold ${statusColor[q.status] || ""}`}>
                    {q.status}
                  </span>
                </div>
                <div className="mt-1 h-1.5 w-full overflow-hidden rounded bg-panel-alt">
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
                  {q.status === "running" && speeds[q.id]?.pps > 0 && (
                    <span className="text-sky-300/80">
                      {fmtRate(speeds[q.id].pps)} pages/s
                      {speeds[q.id].bps > 0 && ` · ${fmtMB(speeds[q.id].bps)} MB/s`}
                      {speeds[q.id].eta && ` · ETA ${fmtEta(speeds[q.id].eta)}`}
                    </span>
                  )}
                  {q.bytes_done > 0 && (
                    <span>{(q.bytes_done / 1048576).toFixed(1)} MB</span>
                  )}
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
                <button
                  className="btn btn-ghost text-xs"
                  disabled={index <= 0}
                  onClick={() => moveUp(index)}
                  title="move up in queue"
                >
                  ▲
                </button>
                <button
                  className="btn btn-ghost text-xs"
                  disabled={index >= (items?.length || 0) - 1}
                  onClick={() => moveDown(index)}
                  title="move down in queue"
                >
                  ▼
                </button>
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
