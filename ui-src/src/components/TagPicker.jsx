import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";

const keyOf = (t) => `${t.type}:${t.name}`;
const LIMIT = 50;

// Tag multi-select dropdown (Big Pickle Phase 1).
// - searches nhentai's live tag list (empty query = most popular tags), so it
//   works as a discovery tool before anything is saved locally
// - multiple selection (all selected tags are matched — AND — via the query)
// - double-click (or the − button) toggles a tag on the blacklist
// - selected/blacklisted tags stay visible at the top of the list
// - does NOT trigger searches; the parent owns query execution/submit
export default function TagPicker({ selected = [], blacklisted = [], onSelect, onBlacklist }) {
  const [open, setOpen] = useState(false);
  const [tags, setTags] = useState([]);
  const [loading, setLoading] = useState(false);
  const [source, setSource] = useState("");
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const seq = useRef(0);

  const toggleOpen = () => {
    if (open) {
      setOpen(false);
      setFilter("");
      setError("");
    } else {
      setError("");
      setOpen(true);
    }
  };

  // Live tag search: one request on open (popular tags), then debounced while
  // typing. The server caches results and falls back to local tags offline.
  useEffect(() => {
    if (!open) return;
    const q = filter.trim();
    const id = ++seq.current;
    setLoading(true);
    const timer = setTimeout(
      () => {
        api
          .get("/api/tags/search", { q, limit: LIMIT })
          .then((res) => {
            if (id !== seq.current) return;
            setTags(res.items || []);
            setSource(res.source || "");
            setError(res.source === "local" ? "live tags unavailable — showing local tags" : "");
          })
          .catch((e) => {
            if (id !== seq.current) return;
            setError(e.message);
          })
          .finally(() => {
            if (id === seq.current) setLoading(false);
          });
      },
      q ? 450 : 0
    );
    return () => clearTimeout(timer);
  }, [open, filter]);

  const selectedKeys = useMemo(() => new Set(selected.map(keyOf)), [selected]);
  const blacklistedKeys = useMemo(() => new Set(blacklisted.map(keyOf)), [blacklisted]);

  const ordered = useMemo(() => {
    const byKey = new Map(tags.map((t) => [keyOf(t), t]));
    // remembered selections may predate the current result page, so fall back
    // to the stored {type, name} object when the tag is missing.
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
    const pinned = ordered.filter(
      (t) => selectedKeys.has(keyOf(t)) || blacklistedKeys.has(keyOf(t))
    );
    if (!needle) return ordered;
    const pinnedKeys = new Set(pinned.map(keyOf));
    const matches = ordered.filter(
      (t) => !pinnedKeys.has(keyOf(t)) && t.name.toLowerCase().includes(needle)
    );
    return [...pinned, ...matches];
  }, [ordered, filter, selectedKeys, blacklistedKeys]);

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
        onClick={toggleOpen}
      >
        🏷 tags
        {selected.length > 0 ? ` (${selected.length} selected)` : ""}
        {blacklisted.length > 0 ? ` · ${blacklisted.length} blocked` : ""}
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-full overflow-hidden rounded-md border border-line-strong bg-panel-alt shadow-xl">
          <input
            className="w-full rounded-none border-0 border-b border-line-strong bg-transparent px-3 py-1.5 text-xs"
            placeholder="search nhentai tags…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            autoFocus
          />
          <div className="max-h-72 overflow-y-auto">
            {visible.map((t) => {
              const k = keyOf(t);
              const isSel = selectedKeys.has(k);
              const isBl = blacklistedKeys.has(k);
              const count = t.gallery_count || t.count || 0;
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
                    {count > 0 && <span className="ml-1 opacity-50">{count}</span>}
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
            })}
            {loading && (
              <div className="px-3 py-1 text-[10px] text-slate-500">searching nhentai…</div>
            )}
            {!loading && visible.length === 0 && (
              <div className="p-2 text-xs text-slate-500">
                {filter.trim() ? "no matching tags" : "no tags found"}
              </div>
            )}
          </div>
          <div className="flex items-center justify-between border-t border-line-strong px-3 py-1 text-[10px] text-slate-500">
            <span>{source === "live" ? "live · nhentai" : source === "local" ? "local tags" : ""}</span>
            <span>{error}</span>
          </div>
        </div>
      )}
    </div>
  );
}
