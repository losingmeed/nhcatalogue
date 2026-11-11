import { useCallback, useEffect, useMemo, useState } from "react";
import { api, mediaUrl } from "../api.js";
import AsyncImage from "../components/AsyncImage.jsx";
import Rule34TagPicker from "../components/Rule34TagPicker.jsx";
import Rule34Viewer from "./Rule34Viewer.jsx";

const SELECTED_KEY = "nhc_r34_selected";
const BLACKLIST_KEY = "nhc_r34_blacklist";
const MATCH_KEY = "nhc_r34_match";

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

// Rule 34's API matches plain tag names only — a "namespace:" prefix makes
// the token unmatchable. Each tag name belongs to exactly one namespace, so
// the plain name is unambiguous; the type is kept for display only.
function tokenFor(tag, exclude = false) {
  const name = /[\s"]/.test(tag.name) ? `"${tag.name.replace(/"/g, '\\"')}"` : tag.name;
  return `${exclude ? "-" : ""}${name}`;
}

function parseTagToken(tok) {
  let body = tok || "";
  const exclude = body.startsWith("-");
  if (exclude) body = body.slice(1);
  // Tolerate a namespace prefix in a query typed by hand.
  const prefix = body.match(/^([A-Za-z]+):(.+)$/);
  if (
    prefix &&
    ["general", "artist", "copyright", "character", "series", "metadata", "other", "tag"].includes(
      prefix[1].toLowerCase()
    )
  ) {
    body = prefix[2];
  }
  if (body.startsWith('"') && body.endsWith('"')) {
    body = body.slice(1, -1).replace(/\\"/g, '"');
  }
  if (!body) return null;
  return { type: "general", name: body, exclude };
}

const sameTag = (a, b) => a.name === b.name;

function dedupeTags(tags) {
  const seen = new Set();
  return tags.filter((t) => {
    const k = t.name;
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

function isTagToken(tok, tag) {
  const parsed = parseTagToken(tok);
  return parsed ? parsed.name === tag.name : false;
}

function readStored(key) {
  try {
    const raw = localStorage.getItem(key);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.filter((t) => t && t.type && t.name) : [];
  } catch {
    return [];
  }
}

function initialState() {
  const selected = readStored(SELECTED_KEY);
  const blacklisted = readStored(BLACKLIST_KEY);
  const query = [...selected.map((t) => tokenFor(t)), ...blacklisted.map((t) => tokenFor(t, true))].join(
    " "
  );
  return { query, selected, blacklisted };
}

export default function Rule34Browse() {
  const initial = useMemo(initialState, []);
  const [query, setQuery] = useState(initial.query);
  const [items, setItems] = useState([]);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState(null);
  const [selectedTags, setSelectedTags] = useState(initial.selected);
  const [blacklistTags, setBlacklistTags] = useState(initial.blacklisted);
  const [viewing, setViewing] = useState(-1);
  const [match, setMatch] = useState(() => {
    try {
      return localStorage.getItem(MATCH_KEY) === "or" ? "or" : "and";
    } catch {
      return "and";
    }
  });

  useEffect(() => {
    api
      .get("/api/rule34/status")
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(SELECTED_KEY, JSON.stringify(selectedTags));
    } catch {
      /* storage unavailable */
    }
  }, [selectedTags]);

  useEffect(() => {
    try {
      localStorage.setItem(BLACKLIST_KEY, JSON.stringify(blacklistTags));
    } catch {
      /* storage unavailable */
    }
  }, [blacklistTags]);

  const load = useCallback(
    async (p, replace) => {
      setLoading(true);
      setError("");
      try {
        const res = await api.get("/api/search", { q: query, page: p, source: "rule34", match });
        setItems((prev) => (replace ? res.results || [] : [...prev, ...(res.results || [])]));
      } catch (e) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    },
    [query, match]
  );

  const submit = useCallback(() => {
    const tags = tagsFromQuery(query);
    setSelectedTags(tags.selected);
    setBlacklistTags(tags.blacklisted);
    setPage(1);
    setViewing(-1);
    load(1, true);
  }, [load, query]);

  // remember the tag match mode (and → all tags must match, or → any tag)
  useEffect(() => {
    try {
      localStorage.setItem(MATCH_KEY, match);
    } catch {
      /* storage unavailable */
    }
  }, [match]);

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
    setQuery(tokens.filter(Boolean).join(" "));
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
    setQuery(tokens.filter(Boolean).join(" "));
  };

  if (viewing >= 0 && items[viewing]) {
    return (
      <div className="h-full">
        <Rule34Viewer
          items={items}
          index={viewing}
          onIndexChange={setViewing}
          onClose={() => setViewing(-1)}
        />
      </div>
    );
  }

  return (
    <div className="p-6">
      {status?.needs_credentials && (
        <div className="mb-4 rounded border border-amber-700 bg-amber-950/40 px-3 py-2 text-sm text-amber-200">
          {status.message} — get one from{" "}
          <a
            className="underline"
            href="https://rule34.xxx/index.php?page=account&s=options"
            target="_blank"
            rel="noreferrer"
          >
            rule34 account options
          </a>
          , then fill in <b>rule34 user id</b> and <b>rule34 api key</b> in Settings.
        </div>
      )}

      <div className="relative mb-4 flex flex-wrap items-center gap-2">
        <div className="relative min-w-[320px] flex-1">
          <input
            className="w-full"
            placeholder='rule34 — e.g. 1girl solo "long tag" -anal'
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
            }}
          />
          <Rule34TagPicker
            selected={selectedTags}
            blacklisted={blacklistTags}
            onSelect={toggleTag}
            onBlacklist={toggleBlacklist}
          />
        </div>
        <select
          value={match}
          onChange={(e) => setMatch(e.target.value)}
          className="w-32"
          title="how multiple tags combine: all = every tag must match, any = at least one"
        >
          <option value="and">match: all</option>
          <option value="or">match: any</option>
        </select>
        <button className="btn btn-primary text-sm" onClick={submit}>
          search
        </button>
      </div>

      {query && (
        <div className="mb-3 text-xs text-slate-500">
          rule34 search:
          {splitTokens(query).map((tok, i) => (
            <span key={i} className="ml-1 rounded bg-slate-800 px-1.5 py-0.5">
              {tok}
            </span>
          ))}
        </div>
      )}

      {error && (
        <div className="card mb-4 border-rose-800 p-3 text-sm text-rose-300">{error}</div>
      )}

      {items.length === 0 ? (
        <div className="p-10 text-center text-sm text-slate-500">
          {loading ? "loading…" : "no results — pick some tags and press search"}
        </div>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
          {items.map((g, i) => (
            <div
              key={g.id}
              className="card group cursor-pointer transition-transform hover:-translate-y-0.5 hover:border-indigo-600/60"
              onClick={() => setViewing(i)}
            >
              <div className="relative aspect-[5/7] w-full overflow-hidden bg-panel-deep">
                <AsyncImage
                  src={mediaUrl(`/api/rule34/cover/${g.id}`)}
                  alt={g.title}
                  eager
                  wrapperClassName="h-full w-full"
                  className="h-full w-full object-cover"
                />
                {g.media_kind === "video" && (
                  <span className="absolute left-1 top-1 rounded bg-black/70 px-1.5 text-[10px]">
                    ▶ video
                  </span>
                )}
                {g.in_catalogue && (
                  <span className="absolute right-1 top-1 rounded bg-indigo-600/90 px-1.5 text-[10px] font-bold text-white">
                    CAT
                  </span>
                )}
              </div>
              <div className="p-2">
                <div className="line-clamp-2 min-h-8 text-xs leading-4 text-slate-200" title={g.title}>
                  {g.title}
                </div>
                <div className="mt-1 flex flex-wrap gap-1 text-[10px] text-slate-500">
                  <span>post {g.media_id}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="mt-4 flex items-center justify-between text-xs text-slate-500">
        <span>{loading ? "loading…" : `${items.length} loaded`}</span>
        {items.length > 0 && (
          <button
            className="btn btn-ghost text-xs"
            onClick={() => {
              const next = page + 1;
              setPage(next);
              load(next, false);
            }}
            disabled={loading}
          >
            load more
          </button>
        )}
      </div>
    </div>
  );
}
