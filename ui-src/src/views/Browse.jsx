import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, mediaUrl } from "../api.js";
import { GalleryGrid, InfiniteScroll } from "../components/GalleryBits.jsx";
import TagPicker from "../components/TagPicker.jsx";

const SORTS = [
  ["date", "newest"],
  ["popular", "popular"],
  ["popular-month", "this month"],
  ["popular-week", "this week"],
];

// --- tag query helpers (Big Pickle Phase 1) --------------------------------
// Tag names may contain spaces ("full color"), so tokens are quoted and the
// query is split on whitespace *outside* quotes.
function splitTokens(q) {
  const out = [];
  let cur = "";
  let quoted = false;
  for (const ch of q || "") {
    if (ch === '"') {
      quoted = !quoted;
      cur += ch;
    } else if (/\s/.test(ch) && !quoted) {
      if (cur) out.push(cur);
      cur = "";
    } else {
      cur += ch;
    }
  }
  if (cur) out.push(cur);
  return out;
}

function tokenFor(tag, exclude = false) {
  const name = /[\s"]/.test(tag.name) ? `"${tag.name.replace(/"/g, '\\"')}"` : tag.name;
  // nhentai calls general tags "tag:" in the query syntax
  const type = tag.type === "general" ? "tag" : tag.type;
  return `${exclude ? "-" : ""}${type}:${name}`;
}

function parseTagToken(tok) {
  const m = (tok || "").match(/^(-?)([A-Za-z]+):(.+)$/);
  if (!m) return null;
  let name = m[3];
  if (name.startsWith('"') && name.endsWith('"')) {
    name = name.slice(1, -1).replace(/\\"/g, '"');
  }
  if (!name) return null;
  const type = m[2].toLowerCase();
  return { type: type === "tag" ? "general" : type, name, exclude: m[1] === "-" };
}

const sameTag = (a, b) => a.type === b.type && a.name === b.name;

function dedupeTags(tags) {
  const seen = new Set();
  return tags.filter((t) => {
    const k = `${t.type}:${t.name}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

function tagsFromQuery(q) {
  const parsed = splitTokens(q).map(parseTagToken).filter(Boolean);
  return {
    selected: dedupeTags(parsed.filter((t) => !t.exclude)),
    blacklisted: dedupeTags(parsed.filter((t) => t.exclude)),
  };
}

// true when a query token refers to the tag (either polarity)
function isTagToken(tok, tag) {
  const body = tok.startsWith("-") ? tok.slice(1) : tok;
  const plain = `${tag.type}:${tag.name}`;
  return body === plain || body === tokenFor(tag);
}

function readStoredTags(key) {
  try {
    const raw = localStorage.getItem(key);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.filter((t) => t && t.type && t.name) : [];
  } catch {
    return [];
  }
}

// Restore remembered tag selection and/or a deep-linked query on first render.
function initialBrowseState() {
  const hashQ = new URLSearchParams(window.location.hash.split("?")[1] || "").get("q") || "";
  const fromQuery = tagsFromQuery(hashQ);
  // an explicit ?q= link wins; otherwise restore the remembered selection
  const selected = hashQ
    ? fromQuery.selected
    : dedupeTags([...readStoredTags("nhc_browse_selected"), ...fromQuery.selected]);
  const blacklisted = hashQ
    ? fromQuery.blacklisted
    : dedupeTags([...readStoredTags("nhc_browse_blacklist"), ...fromQuery.blacklisted]);
  const query =
    hashQ ||
    [...selected.map((t) => tokenFor(t)), ...blacklisted.map((t) => tokenFor(t, true))].join(" ");
  return { query, selected, blacklisted };
}

// Browse view: submit-driven live search with tag multi-select + blacklist,
// infinite scroll, sort (Big Pickle Phase 1).
export default function Browse() {
  const initial = useMemo(initialBrowseState, []);
  const [query, setQuery] = useState(initial.query);
  const [sort, setSort] = useState("date");
  const [items, setItems] = useState([]);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const sentinel = useRef(null);

  const [selectedTags, setSelectedTags] = useState(initial.selected);
  const [blacklistTags, setBlacklistTags] = useState(initial.blacklisted);

  // remember previously selected / blacklisted tags
  useEffect(() => {
    try {
      localStorage.setItem("nhc_browse_selected", JSON.stringify(selectedTags));
    } catch {
      /* storage unavailable — keep in-memory only */
    }
  }, [selectedTags]);

  useEffect(() => {
    try {
      localStorage.setItem("nhc_browse_blacklist", JSON.stringify(blacklistTags));
    } catch {
      /* storage unavailable — keep in-memory only */
    }
  }, [blacklistTags]);

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

  // mount-only initial load (deep link / remembered tags); all other searches
  // run on explicit submit.
  useEffect(() => {
    if (initial.query) load(1, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const submit = useCallback(() => {
    const tags = tagsFromQuery(query);
    setSelectedTags(tags.selected);
    setBlacklistTags(tags.blacklisted);
    setPage(1);
    load(1, true);
  }, [load, query]);

  const setQueryTokens = (tokens) => setQuery(tokens.filter(Boolean).join(" "));

  const toggleTag = (tag) => {
    const isSelected = selectedTags.some((t) => sameTag(t, tag));
    const tokens = splitTokens(query).filter((tok) => !isTagToken(tok, tag));
    if (isSelected) {
      setSelectedTags((prev) => prev.filter((t) => !sameTag(t, tag)));
    } else {
      setSelectedTags((prev) => [
        ...prev.filter((t) => !sameTag(t, tag)),
        { type: tag.type, name: tag.name },
      ]);
      setBlacklistTags((prev) => prev.filter((t) => !sameTag(t, tag)));
      tokens.push(tokenFor(tag));
    }
    setQueryTokens(tokens);
  };

  const toggleBlacklist = (tag) => {
    const isBlacklisted = blacklistTags.some((t) => sameTag(t, tag));
    const tokens = splitTokens(query).filter((tok) => !isTagToken(tok, tag));
    if (isBlacklisted) {
      setBlacklistTags((prev) => prev.filter((t) => !sameTag(t, tag)));
    } else {
      setBlacklistTags((prev) => [
        ...prev.filter((t) => !sameTag(t, tag)),
        { type: tag.type, name: tag.name },
      ]);
      setSelectedTags((prev) => prev.filter((t) => !sameTag(t, tag)));
      tokens.push(tokenFor(tag, true));
    }
    setQueryTokens(tokens);
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
              if (e.key === "Enter") submit();
            }}
            autoFocus
          />
          <TagPicker
            selected={selectedTags}
            blacklisted={blacklistTags}
            onSelect={toggleTag}
            onBlacklist={toggleBlacklist}
          />
        </div>
        <select value={sort} onChange={(e) => setSort(e.target.value)} className="w-36">
          {SORTS.map(([v, l]) => (
            <option key={v} value={v}>
              sort: {l}
            </option>
          ))}
        </select>
        <button className="btn btn-primary text-sm" onClick={submit}>
          search
        </button>
      </div>

      {query && (
        <div className="mb-3 text-xs text-slate-500">
          live search:
          {splitTokens(query).map((tok, i) => (
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
                    const row = await api.post("/api/catalogue", { id: g.id });
                    g.in_catalogue = row.in_catalogue;
                    g.persisted = row.persisted;
                    setItems([...items]);
                  } catch (err) {
                    alert(err.message);
                  }
                }}
                title="preview without saving — save it from the detail page"
              >
                + preview
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
