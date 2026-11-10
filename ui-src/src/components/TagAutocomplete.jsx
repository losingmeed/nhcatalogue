import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

// Tag autocomplete: suggestions from cached catalogue tags as you type
// (plan §6.1). Understands the `type:` prefix you're currently typing, e.g.
// `artist:mi` → suggests artist tags matching "mi".
export default function TagAutocomplete({ query, onSelect, dropdownClass }) {
  const [suggestions, setSuggestions] = useState([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const wrapRef = useRef(null);
  const lastPart = useRef("");

  // the trailing token of the query, e.g. "artist:mi"
  const token = (query || "").split(/\s+/).pop() || "";
  const m = token.match(/^(-?)(\w*):(.*)$/);
  const exclude = m?.[1] === "-";
  const typeFilter = m?.[2] || "";
  const frag = m ? m[3] : token;

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      if (frag.length < 1 || /[" ]$/.test(token)) {
        setSuggestions([]);
        return;
      }
      try {
        const res = await api.get("/api/tags/suggest", { q: frag, limit: 8 });
        let items = res.items || [];
        if (typeFilter) items = items.filter((t) => t.type === typeFilter);
        if (!cancelled) {
          setSuggestions(items.slice(0, 8));
          setOpen(items.length > 0);
          setActive(0);
        }
      } catch {
        /* silent */
      }
    };
    const t = setTimeout(run, 120);
    return () => {
      clearTimeout(t);
      cancelled = true;
    };
  }, [frag, typeFilter, token]);

  useEffect(() => {
    const onKey = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(document.activeElement) && !open) return;
      if (!open) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive((a) => Math.min(a + 1, suggestions.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive((a) => Math.max(a - 1, 0));
      } else if (e.key === "Enter" && suggestions[active]) {
        e.preventDefault();
        e.stopPropagation();
        onSelect?.(suggestions[active], { exclude, replaceToken: true });
        setOpen(false);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, active, suggestions, exclude, onSelect]);

  if (!open || !suggestions.length) return null;
  return (
    <div
      ref={wrapRef}
      className={`absolute z-50 mt-1 w-full overflow-hidden rounded-md border border-[#2a2f3d] bg-[#171b25] shadow-xl ${dropdownClass || ""}`}
    >
      {suggestions.map((s, i) => (
        <button
          key={`${s.type}:${s.id}`}
          className={`flex w-full items-center justify-between px-3 py-1.5 text-left text-xs ${
            i === active ? "bg-indigo-600/30" : "hover:bg-slate-700/30"
          }`}
          onMouseEnter={() => setActive(i)}
          onClick={(e) => {
            e.preventDefault();
            onSelect?.(s, { exclude, replaceToken: true });
            setOpen(false);
          }}
        >
          <span>
            <span className="text-slate-500">{s.type}:</span>
            <span className="text-slate-100">{s.name}</span>
          </span>
          <span className="text-slate-500">{s.count > 0 ? `${s.count} locally` : ""}</span>
        </button>
      ))}
    </div>
  );
}
