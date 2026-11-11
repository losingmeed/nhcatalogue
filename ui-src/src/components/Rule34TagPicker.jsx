import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";

const keyOf = (t) => `${t.type}:${t.name}`;
const LIMIT = 50;

export default function Rule34TagPicker({
  selected = [],
  blacklisted = [],
  onSelect,
  onBlacklist,
}) {
  const [open, setOpen] = useState(false);
  const [tags, setTags] = useState([]);
  const [loading, setLoading] = useState(false);
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

  useEffect(() => {
    if (!open) return;
    const q = filter.trim();
    const id = ++seq.current;
    setLoading(true);
    const timer = setTimeout(
      () => {
        api
          .get("/api/tags/search", { q, limit: LIMIT, source: "rule34" })
          .then((res) => {
            if (id !== seq.current) return;
            setTags(res.items || []);
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
    const selTags = selected.map((t) => byKey.get(keyOf(t)) || t);
    const blTags = blacklisted.map((t) => byKey.get(keyOf(t)) || t);
    const selSet = new Set(selectedKeys);
    const blSet = new Set(blacklistedKeys);
    const rest = tags
      .filter((t) => !selSet.has(keyOf(t)) && !blSet.has(keyOf(t)))
      .sort((a, b) => (b.count ?? 0) - (a.count ?? 0));
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
    if (blacklistedKeys.has(keyOf(t))) onBlacklist?.(t);
    else onSelect?.(t);
  };

  return (
    <div className="relative mt-1">
      <button
        type="button"
        className="btn btn-ghost w-full justify-start text-xs"
        onClick={toggleOpen}
      >
        🏷 rule34 tags
        {selected.length > 0 ? ` (${selected.length} selected)` : ""}
        {blacklisted.length > 0 ? ` · ${blacklisted.length} blocked` : ""}
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-full overflow-hidden rounded-md border border-line-strong bg-panel-alt shadow-xl">
          <input
            className="w-full rounded-none border-0 border-b border-line-strong bg-transparent px-3 py-1.5 text-xs"
            placeholder="search rule34 tags…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            autoFocus
          />
          <div className="max-h-72 overflow-y-auto">
            {visible.map((t) => {
              const k = keyOf(t);
              const isSel = selectedKeys.has(k);
              const isBl = blacklistedKeys.has(k);
              const count = t.count || 0;
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
                  onDoubleClick={() => onBlacklist?.(t)}
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
                        onBlacklist?.(t);
                      }}
                    >
                      {isBl ? "×" : "−"}
                    </button>
                  </span>
                </div>
              );
            })}
            {loading && (
              <div className="px-3 py-1 text-[10px] text-slate-500">searching rule34…</div>
            )}
            {!loading && visible.length === 0 && (
              <div className="p-2 text-xs text-slate-500">
                {filter.trim() ? "no matching tags" : "no tags found"}
              </div>
            )}
          </div>
          <div className="flex items-center justify-between border-t border-line-strong px-3 py-1 text-[10px] text-slate-500">
            <span>{error}</span>
            <span>rule34</span>
          </div>
        </div>
      )}
    </div>
  );
}
