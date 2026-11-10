import { useCallback, useEffect, useRef, useState } from "react";
import { api, pageUrl } from "../api.js";
import { navigate } from "../App.jsx";

// Reader (plan §6.3): single/double page, long-strip, RTL toggle, keyboard
// nav (←/→/space/Home/End), preload next pages, remembers position per
// gallery (PATCHed to /api/catalogue/{id}/progress).

const MODES = [
  ["single", "single"],
  ["double", "double"],
  ["strip", "long strip"],
];

export default function Reader({ id }) {
  const [info, setInfo] = useState(null);
  const [page, setPage] = useState(1);
  const [mode, setMode] = useState(() => localStorage.getItem("nhc_reader_mode") || "single");
  const [rtl, setRtl] = useState(() => localStorage.getItem("nhc_reader_rtl") === "1");
  const [preload, setPreload] = useState(() => +(localStorage.getItem("nhc_reader_preload") || 3));
  const [error, setError] = useState("");
  const [showBar, setShowBar] = useState(true);
  const stripRef = useRef(null);
  const saveTimer = useRef(null);

  useEffect(() => {
    api
      .get(`/api/reader/${id}/info`)
      .then((res) => {
        setInfo(res);
        const startPage = +(new URLSearchParams(window.location.hash.split("?")[1] || "").get("page") || 0);
        setPage(startPage || Math.min(res.read_progress_pages + 1, res.num_pages) || 1);
      })
      .catch((e) => setError(e.message));
  }, [id]);

  // persist prefs
  useEffect(() => {
    localStorage.setItem("nhc_reader_mode", mode);
    localStorage.setItem("nhc_reader_rtl", rtl ? "1" : "0");
    localStorage.setItem("nhc_reader_preload", String(preload));
  }, [mode, rtl, preload]);

  // save read progress (debounced)
  useEffect(() => {
    if (!info || page < 1) return;
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      api.patch(`/api/catalogue/${id}/progress`, { page }).catch(() => {});
    }, 800);
  }, [page, id, info]);

  const total = info?.num_pages || 0;

  const go = useCallback(
    (delta) => {
      setPage((p) => {
        const next = p + (rtl ? -delta : delta);
        return Math.max(1, Math.min(total, next));
      });
    },
    [rtl, total]
  );

  // keyboard nav
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "ArrowRight") go(1);
      else if (e.key === "ArrowLeft") go(-1);
      else if (e.key === " ") {
        e.preventDefault();
        go(1);
      } else if (e.key === "Home") setPage(1);
      else if (e.key === "End") setPage(total);
      else if (e.key === "Escape") navigate(`/gallery/${id}`);
      else if (e.key === "b") setShowBar((s) => !s);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, total, id]);

  // preload the next N pages
  useEffect(() => {
    if (!info) return;
    for (let i = 1; i <= preload; i++) {
      const n = page + i;
      if (n <= total) {
        const img = new Image();
        img.src = pageUrl(id, n);
      }
    }
    // preload the double-page partner
    if (mode === "double" && page + 1 <= total) {
      const img = new Image();
      img.src = pageUrl(id, page + 1);
    }
  }, [page, preload, mode, total, id, info]);

  // scroll to top on page change (single/double)
  useEffect(() => {
    if (mode !== "strip" && stripRef.current) stripRef.current.scrollTop = 0;
  }, [page, mode]);

  if (error) return <div className="p-6 text-sm text-rose-400">{error}</div>;
  if (!info) return <div className="p-6 text-slate-500">loading…</div>;
  if (!total) return <div className="p-6 text-slate-500">no pages found for #{id}</div>;

  const dir = rtl ? "rtl" : "ltr";

  const PageImg = ({ n }) =>
    n >= 1 && n <= total ? (
      <img
        key={n}
        src={pageUrl(id, n)}
        alt={`page ${n}`}
        className="mx-auto max-h-[calc(100vh-120px)] w-auto max-w-full select-none object-contain"
        loading="eager"
      />
    ) : (
      <div className="flex h-[60vh] w-1/2 items-center justify-center text-slate-600">—</div>
    );

  return (
    <div className="relative flex h-full flex-col" ref={stripRef}>
      {/* top toolbar */}
      {showBar && (
        <div className="flex flex-wrap items-center gap-3 border-b border-[#222734] bg-[#0e1118] px-4 py-2 text-sm">
          <button className="btn btn-ghost" onClick={() => navigate(`/gallery/${id}`)}>
            ← back
          </button>
          <span className="text-xs text-slate-400">
            {info.title} · #{id}
          </span>
          <span className="ml-auto tabular-nums text-xs">
            {page} / {total}
          </span>
          <select value={mode} onChange={(e) => setMode(e.target.value)} className="w-28">
            {MODES.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
          <button className="btn btn-ghost" onClick={() => setRtl(!rtl)} title="right-to-left">
            {rtl ? "→ RTL" : "LTR ←"}
          </button>
          <select
            value={preload}
            onChange={(e) => setPreload(+e.target.value)}
            className="w-24"
            title="pages to preload"
          >
            {[0, 1, 3, 5, 8].map((n) => (
              <option key={n} value={n}>
                preload {n}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* page area */}
      {mode === "strip" ? (
        <div className="flex-1 overflow-y-auto py-2" dir={dir}>
          {Array.from({ length: total }, (_, i) => i + 1).map((n) => (
            <div
              key={n}
              data-page={n}
              ref={(el) => {
                // track visible page for progress saving
                if (!el) return;
                const io = new IntersectionObserver((entries) => {
                  if (entries[0].isIntersecting) setPage(n);
                }, { threshold: 0.4 });
                io.observe(el);
                el._io = io;
              }}
            >
              <PageImg n={n} />
            </div>
          ))}
        </div>
      ) : (
        <div
          className="flex flex-1 items-center justify-center gap-1 overflow-hidden py-4"
          dir={dir}
          onClick={(e) => {
            // click left/right thirds to page
            const x = e.clientX / window.innerWidth;
            if (x < 0.3) go(-1);
            else if (x > 0.7) go(1);
          }}
        >
          {mode === "double" ? (
            <div className="flex gap-1">
              <PageImg n={page} />
              <PageImg n={page + 1} />
            </div>
          ) : (
            <PageImg n={page} />
          )}
        </div>
      )}

      {/* bottom bar */}
      <div className="flex items-center gap-3 border-t border-[#222734] bg-[#0e1118] px-4 py-2">
        <button className="btn btn-ghost" onClick={() => go(-1)} disabled={page <= 1}>
          ← prev
        </button>
        <input
          type="range"
          min={1}
          max={total}
          value={page}
          onChange={(e) => setPage(+e.target.value)}
          className="flex-1"
        />
        <button className="btn btn-ghost" onClick={() => go(1)} disabled={page >= total}>
          next →
        </button>
      </div>
    </div>
  );
}
