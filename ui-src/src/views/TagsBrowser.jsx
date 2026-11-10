import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { GalleryGrid } from "../components/GalleryBits.jsx";
import { navigate } from "../App.jsx";

// Tags browser (plan §6.5 — the primary way to navigate a large library):
// every tag in the catalogue grouped by type with gallery counts, type-ahead
// search box filters tag names live, multi-select → gallery grid filtered by
// those tags.
const TYPE_ORDER = ["artist", "parody", "character", "group", "language", "category", "general"];
const TYPE_LABEL = {
  artist: "Artist",
  parody: "Parody",
  character: "Characters",
  group: "Groups",
  language: "Language",
  category: "Category",
  general: "General",
};

export default function TagsBrowser() {
  const [groups, setGroups] = useState(null);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState([]); // [{id, type, name}]

  useEffect(() => {
    api.get("/api/tags/groups").then(setGroups).catch(() => setGroups({}));
  }, []);

  const filtered = useMemo(() => {
    if (!groups) return null;
    const needle = (q || "").toLowerCase().replace(/[\s_-]+/g, " ").trim();
    const out = {};
    for (const [type, tags] of Object.entries(groups)) {
      const kept = tags.filter((t) => {
        if (!needle) return true;
        const norm = t.name_norm || t.name.toLowerCase().replace(/[\s_-]+/g, " ");
        return norm.includes(needle);
      });
      if (kept.length) out[type] = kept;
    }
    return out;
  }, [groups, q]);

  const toggle = (tag) => {
    setSelected((prev) =>
      prev.some((t) => t.id === tag.id)
        ? prev.filter((t) => t.id !== tag.id)
        : [...prev, tag]
    );
  };

  // galleries carrying ANY of the selected tags (uses /api/tags/{id}/galleries
  // per tag and merges)
  const [galleries, setGalleries] = useState(null);
  const [loadingG, setLoadingG] = useState(false);
  useEffect(() => {
    if (!selected.length) {
      setGalleries(null);
      return;
    }
    let cancelled = false;
    setLoadingG(true);
    Promise.all(selected.map((t) => api.get(`/api/tags/${t.id}/galleries`, { limit: 200 })))
      .then((rs) => {
        if (cancelled) return;
        const byId = new Map();
        rs.forEach((r) => r.items.forEach((g) => byId.set(g.id, g)));
        setGalleries([...byId.values()]);
      })
      .catch(() => setGalleries([]))
      .finally(() => !cancelled && setLoadingG(false));
    return () => {
      cancelled = true;
    };
  }, [selected]);

  if (!filtered) return <div className="p-6 text-slate-500">loading tags…</div>;

  const totalTags = Object.values(filtered).reduce((n, ts) => n + ts.length, 0);

  return (
    <div className="p-6">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">Tags</h1>
        <input
          className="min-w-64 flex-1"
          placeholder="type-ahead filter tag names… (full color, artist:mi, …)"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <span className="text-xs text-slate-500">{totalTags} tags</span>
        {selected.length > 0 && (
          <button className="btn btn-ghost text-xs" onClick={() => setSelected([])}>
            clear selection ({selected.length})
          </button>
        )}
      </div>

      {selected.length > 0 && (
        <div className="mb-6 card p-4">
          <div className="mb-2 flex flex-wrap gap-1.5">
            {selected.map((t) => (
              <span
                key={t.id}
                className="inline-flex items-center gap-1 rounded-full border border-indigo-700/50 bg-indigo-600/25 px-2 py-0.5 text-xs text-indigo-200"
              >
                {t.type}: {t.name}
                <button className="opacity-60 hover:opacity-100" onClick={() => toggle(t)}>
                  ×
                </button>
              </span>
            ))}
          </div>
          {loadingG ? (
            <div className="text-sm text-slate-500">finding galleries…</div>
          ) : galleries ? (
            <>
              <div className="mb-2 text-xs text-slate-500">
                {galleries.length} galleries carry these tags
              </div>
              <GalleryGrid items={galleries} empty="no galleries carry all these tags" />
            </>
          ) : null}
        </div>
      )}

      {TYPE_ORDER.filter((t) => filtered[t]?.length).map((type) => (
        <section key={type} className="mb-8">
          <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
            {TYPE_LABEL[type] || type}
            <span className="text-xs font-normal text-slate-600">
              {filtered[type].length} tags
            </span>
          </h2>
          <div className="flex flex-wrap gap-1.5">
            {filtered[type].slice(0, 400).map((t) => {
              const isSel = selected.some((s) => s.id === t.id);
              return (
                <button
                  key={t.id}
                  onClick={() => toggle(t)}
                  className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
                    isSel
                      ? "border-indigo-500 bg-indigo-600/40 text-white"
                      : "border-line-strong bg-panel text-slate-300 hover:border-indigo-600/60"
                  }`}
                >
                  {t.name}
                  <span className="ml-1.5 text-[10px] opacity-60">{t.gallery_count}</span>
                </button>
              );
            })}
          </div>
        </section>
      ))}
      {totalTags === 0 && (
        <div className="p-10 text-center text-sm text-slate-500">
          no tags in your catalogue yet — add galleries first
        </div>
      )}
    </div>
  );
}
