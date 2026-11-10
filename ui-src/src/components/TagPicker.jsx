import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";

const keyOf = (t) => `${t.type}:${t.name}`;

// Tag multi-select dropdown (Big Pickle Phase 1).
// - combined list of available tags (no typing required to find them)
// - multiple selection (all selected tags are matched — AND — via the query)
// - double-click (or the − button) toggles a tag on the blacklist
// - selected/blacklisted tags stay visible at the top of the list
// - does NOT trigger searches; the parent owns query execution/submit
export default function TagPicker({ selected = [], blacklisted = [], onSelect, onBlacklist }) {
  const [open, setOpen] = useState(false);
  const [tags, setTags] = useState([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    api
      .get("/api/tags", { q: "", limit: 1000 })
      .then((res) => {
        if (!cancelled) setTags(res.items || []);
      })
      .catch(() => {
        if (!cancelled) setTags([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const selectedKeys = useMemo(() => new Set(selected.map(keyOf)), [selected]);
  const blacklistedKeys = useMemo(() => new Set(blacklisted.map(keyOf)), [blacklisted]);

  const ordered = useMemo(() => {
    const byKey = new Map(tags.map((t) => [keyOf(t), t]));
    // remembered selections may predate the current catalogue snapshot, so
    // fall back to the stored {type, name} object when the tag is missing.
    const selTags = selected.map((t) => byKey.get(keyOf(t)) || t);
    const blTags = blacklisted.map((t) => byKey.get(keyOf(t)) || t);
    const selSet = new Set(selectedKeys);
    const blSet = new Set(blacklistedKeys);
    const rest = tags
      .filter((t) => !selSet.has(keyOf(t)) && !blSet.has(keyOf(t)))
      .sort(
        (a, b) =>
          (b.gallery_count ?? b.count ?? 0) - (a.gallery_count ?? a.count ?? 0)
      );
    return [...selTags, ...blTags, ...rest];
  }, [tags, selected, blacklisted, selectedKeys, blacklistedKeys]);

  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return ordered;
    return ordered.filter((t) => t.name.toLowerCase().includes(needle));
  }, [ordered, filter]);

  const toggle = (t) => {
    // clicking a blacklisted row removes it from the blacklist
    if (blacklistedKeys.has(keyOf(t))) onBlacklist?.(t);
    else onSelect?.(t);
  };

  const blacklist = (t) => onBlacklist?.(t);

  return (
    <div className="relative mt-1">
      <button
        type="button"
        className="btn btn-ghost w-full justify-start text-xs"
        onClick={() => setOpen((o) => !o)}
      >
        🏷 tags
        {selected.length > 0 ? ` (${selected.length} selected)` : ""}
        {blacklisted.length > 0 ? ` · ${blacklisted.length} blocked` : ""}
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-full overflow-hidden rounded-md border border-[#2a2f3d] bg-[#171b25] shadow-xl">
          <input
            className="w-full rounded-none border-0 border-b border-[#2a2f3d] bg-transparent px-3 py-1.5 text-xs"
            placeholder="filter tags…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            autoFocus
          />
          <div className="max-h-72 overflow-y-auto">
            {loading ? (
              <div className="p-2 text-xs text-slate-500">loading tags…</div>
            ) : (
              visible.map((t) => {
                const k = keyOf(t);
                const isSel = selectedKeys.has(k);
                const isBl = blacklistedKeys.has(k);
                return (
                  <div
                    key={k}
                    className={`flex items-center justify-between px-3 py-1.5 text-xs ${
                      isSel
                        ? "bg-indigo-600/30"
                        : isBl
                          ? "bg-rose-900/40 text-rose-200"
                          : "hover:bg-slate-700/30"
                    }`}
                    onClick={() => toggle(t)}
                    onDoubleClick={() => blacklist(t)}
                    title={isBl ? "click to un-blacklist" : "double-click to blacklist"}
                  >
                    <span className="min-w-0 flex-1 truncate">
                      {t.type !== "general" && <span className="text-slate-500">{t.type}: </span>}
                      {t.name}
                      {(t.gallery_count ?? 0) > 0 && (
                        <span className="ml-1 opacity-50">{t.gallery_count}</span>
                      )}
                    </span>
                    <span className="ml-2 flex items-center gap-2">
                      {isBl && <span className="text-[10px] text-rose-300">blacklisted</span>}
                      <button
                        type="button"
                        className={`px-1 ${
                          isBl
                            ? "text-rose-300 hover:text-rose-100"
                            : "text-slate-500 hover:text-rose-300"
                        }`}
                        title={isBl ? "remove from blacklist" : "blacklist tag"}
                        onClick={(e) => {
                          e.stopPropagation();
                          blacklist(t);
                        }}
                      >
                        {isBl ? "×" : "−"}
                      </button>
                    </span>
                  </div>
                );
              })
            )}
            {!loading && visible.length === 0 && (
              <div className="p-2 text-xs text-slate-500">
                {tags.length ? "no tags match the filter" : "no tags yet"}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
