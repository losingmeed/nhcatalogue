import { useCallback, useEffect, useRef, useState } from "react";
import { api, mediaUrl } from "../api.js";
import { GalleryGrid, InfiniteScroll, TagPill } from "../components/GalleryBits.jsx";
import TagAutocomplete from "../components/TagAutocomplete.jsx";

const SORTS = [
  ["", "relevance"],
  ["date", "newest"],
  ["popular", "popular"],
  ["popular-month", "this month"],
  ["popular-week", "this week"],
];

// Browse view: live search with full nhentai query syntax + tag autocomplete
// chips as you type, infinite scroll, sort (plan §6.1).
export default function Browse() {
  const [query, setQuery] = useState(
    () => new URLSearchParams(window.location.hash.split("?")[1] || "").get("q") || ""
  );
  const [sort, setSort] = useState("");
  const [items, setItems] = useState([]);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const sentinel = useRef(null);

  useEffect(() => {
    setItems([]);
    setPage(1);
    setError("");
  }, [query, sort]);

  const load = useCallback(
    async (p, replace) => {
      setLoading(true);
      try {
        const res = await api.get("/api/search", { q: query, page: p, sort });
        setTotalPages(res.total_pages || 1);
        setItems((prev) => (replace ? res.results : [...prev, ...res.results]));
      } catch (e) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    },
    [query, sort]
  );

  useEffect(() => {
    load(1, true);
  }, [load]);

  // rebuild the query string from an autocomplete selection
  const applySuggestion = (tag, { exclude }) => {
    const parts = (query || "").split(/\s+/).filter(Boolean);
    parts.pop(); // drop the token being typed
    parts.push(`${exclude ? "-" : ""}${tag.type}:${tag.name}`);
    const q = parts.join(" ");
    setQuery(q);
    window.location.hash = `#/browse?q=${encodeURIComponent(q)}`;
  };

  // preload covers for currently shown galleries (server caches them)
  useEffect(() => {
    if (!items.length) return;
    items.slice(0, 12).forEach((g) => {
      const img = new Image();
      img.src = mediaUrl(g.cover_url || `/api/cover/${g.id}`);
    });
  }, [items]);

  return (
    <div className="p-6">
      <div className="relative mb-4 flex flex-wrap items-center gap-2">
        <div className="relative min-w-[320px] flex-1">
          <input
            className="w-full"
            placeholder='search — e.g. artist:mi language:english "full color" -yaoi'
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                window.location.hash = `#/browse?q=${encodeURIComponent(query)}`;
                load(1, true);
              }
            }}
            autoFocus
          />
          <TagAutocomplete query={query} onSelect={applySuggestion} />
        </div>
        <select value={sort} onChange={(e) => setSort(e.target.value)} className="w-36">
          {SORTS.map(([v, l]) => (
            <option key={v} value={v}>
              sort: {l}
            </option>
          ))}
        </select>
      </div>

      {query && (
        <div className="mb-3 text-xs text-slate-500">
          live search:
          {query.split(/\s+/).map((tok, i) => (
            <span key={i} className="ml-1 rounded bg-slate-800 px-1.5 py-0.5">
              {tok}
            </span>
          ))}
        </div>
      )}

      {error && <div className="card mb-4 border-rose-800 p-3 text-sm text-rose-300">{error}</div>}

      <InfiniteScroll
        hasMore={page < totalPages}
        onLoadMore={() => {
          if (!loading && page < totalPages) {
            const next = page + 1;
            setPage(next);
            load(next, false);
          }
        }}
      >
        <GalleryGrid
          items={items}
          empty={loading ? "" : "no results — check the query syntax or connection status"}
          actions={(g) =>
            g.in_catalogue ? (
              <button
                className="btn btn-ghost text-xs"
                onClick={(e) => {
                  e.stopPropagation();
                  window.location.hash = `#/gallery/${g.id}`;
                }}
              >
                open
              </button>
            ) : (
              <button
                className="btn btn-primary text-xs"
                onClick={async (e) => {
                  e.stopPropagation();
                  e.target.disabled = true;
                  try {
                    await api.post("/api/catalogue", { id: g.id });
                    g.in_catalogue = true;
                    setItems([...items]);
                  } catch (err) {
                    alert(err.message);
                  }
                }}
              >
                + catalogue
              </button>
            )
          }
        />
      </InfiniteScroll>

      <div className="mt-4 flex justify-between text-xs text-slate-500">
        <span>
          {loading ? "loading…" : `${items.length} loaded`} · page {page}/{totalPages || "?"}
        </span>
      </div>
    </div>
  );
}
