import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { GalleryGrid, InfiniteScroll, TagPill } from "../components/GalleryBits.jsx";
import { navigate } from "../App.jsx";

// Library / catalogue grid (plan §6.4): faceted tag filtering with include(+)
// and exclude(−) pills per tag type, FTS search over titles AND tag names,
// favorite/rating/downloaded filters, bulk select → download / collection /
// delete, removable filter pills above the grid.
const TAG_TYPES = ["general", "artist", "parody", "character", "group", "language", "category"];

export default function Library() {
  const [q, setQ] = useState("");
  const [include, setInclude] = useState([]);
  const [exclude, setExclude] = useState([]);
  const [favorite, setFavorite] = useState(null);
  const [ratingMin, setRatingMin] = useState(null);
  const [downloaded, setDownloaded] = useState(null);
  const [sort, setSort] = useState("added");
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(new Set());
  const [collections, setCollections] = useState([]);
  const [facetType, setFacetType] = useState("");
  const [facetTags, setFacetTags] = useState([]);
  const [facetQ, setFacetQ] = useState("");

  const includeParams = include.map((t) => `${t.type}:${t.name}`);
  const excludeParams = exclude.map((t) => `${t.type}:${t.name}`);

  const load = useCallback(
    async (off, replace) => {
      setLoading(true);
      try {
        const res = await api.get("/api/catalogue", {
          q,
          include: includeParams,
          exclude: excludeParams,
          favorite: favorite === null ? "" : favorite,
          rating_min: ratingMin ?? "",
          downloaded: downloaded === null ? "" : downloaded,
          sort,
          limit: 60,
          offset: off,
        });
        setTotal(res.total);
        setItems((prev) => (replace ? res.items : [...prev, ...res.items]));
        if (replace) setSelected(new Set());
      } catch (e) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    },
    [q, includeParams.join(","), excludeParams.join(","), favorite, ratingMin, downloaded, sort]
  );

  useEffect(() => {
    load(0, true);
  }, [load]);

  useEffect(() => {
    api.get("/api/collections").then((r) => setCollections(r.items || [])).catch(() => {});
  }, []);

  // facet sidebar: tags of one type present in the *current* result set
  const loadFacets = useCallback(async () => {
    try {
      const [facets, tags] = await Promise.all([
        api.get("/api/tags/facets", { include: includeParams, exclude: excludeParams }),
        api.get("/api/tags", { type: facetType, q: facetQ, limit: 40 }),
      ]);
      setFacetTags(tags.items || []);
      return facets.types || [];
    } catch {
      return [];
    }
  }, [facetType, facetQ, includeParams, excludeParams]);

  const [facetCounts, setFacetCounts] = useState([]);
  useEffect(() => {
    loadFacets().then(setFacetCounts);
  }, [loadFacets]);

  const toggleFilter = (tag, mode) => {
    const list = mode === "include" ? include : exclude;
    const other = mode === "include" ? exclude : include;
    const setter = mode === "include" ? setInclude : setExclude;
    const otherSetter = mode === "include" ? setExclude : setInclude;
    const exists = list.some((t) => t.type === tag.type && t.name === tag.name);
    if (exists) setter(list.filter((t) => !(t.type === tag.type && t.name === tag.name)));
    else {
      setter([...list, { type: tag.type, name: tag.name }]);
      // a tag can't be both included and excluded
      otherSetter(other.filter((t) => !(t.type === tag.type && t.name === tag.name)));
    }
  };

  const toggleSelect = (id) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const bulk = async (action, extra) => {
    const ids = [...selected];
    if (!ids.length) return;
    try {
      if (action === "download") {
        for (const id of ids) await api.post("/api/downloads", { id });
        navigate("/downloads");
      } else if (action === "collection") {
        for (const id of ids) await api.post(`/api/collections/${extra}/galleries`, { gallery_id: id });
        load(0, true);
      } else if (action === "favorite") {
        for (const id of ids) await api.patch(`/api/catalogue/${id}`, { favorite: extra });
        load(0, true);
      } else if (action === "delete") {
        if (!confirm(`remove ${ids.length} galleries from the catalogue?`)) return;
        const delFiles = confirm("also delete downloaded files from disk?");
        for (const id of ids) await api.del(`/api/catalogue/${id}`, { delete_files: delFiles });
        load(0, true);
      }
      setSelected(new Set());
    } catch (e) {
      alert(e.message);
    }
  };

  const importLib = async () => {
    if (!confirm("scan the library folder and register found galleries?")) return;
    try {
      const r = await api.post("/api/import");
      alert(`imported ${r.imported} galleries`);
      load(0, true);
    } catch (e) {
      alert(e.message);
    }
  };

  const selectAll = () => {
    setSelected(selected.size === items.length ? new Set() : new Set(items.map((g) => g.id)));
  };

  return (
    <div className="flex h-full">
      {/* facet sidebar */}
      <aside className="w-56 shrink-0 space-y-3 overflow-y-auto border-r border-[#222734] bg-[#0e1118] p-3">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          tag filters
        </div>
        <select value={facetType} onChange={(e) => setFacetType(e.target.value)} className="w-full">
          <option value="">all types</option>
          {TAG_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <input
          className="w-full"
          placeholder="type-ahead filter…"
          value={facetQ}
          onChange={(e) => setFacetQ(e.target.value)}
        />
        {facetCounts.length > 0 && (
          <div className="space-y-0.5 text-[10px] text-slate-500">
            {facetCounts.map((f) => (
              <div key={f.type}>
                {f.type}: {f.gallery_count} galleries · {f.tag_count} tags
              </div>
            ))}
          </div>
        )}
        <div className="space-y-1">
          {facetTags.map((t) => {
            const isIncluded = include.some((x) => x.type === t.type && x.name === t.name);
            const isExcluded = exclude.some((x) => x.type === t.type && x.name === t.name);
            return (
              <div
                key={`${t.type}:${t.id}`}
                className={`flex items-center justify-between rounded px-1.5 py-1 text-xs ${
                  isIncluded
                    ? "bg-emerald-900/40 text-emerald-200"
                    : isExcluded
                      ? "bg-rose-900/40 text-rose-200"
                      : "hover:bg-slate-700/30"
                }`}
              >
                <button
                  className="min-w-0 flex-1 truncate text-left"
                  onClick={() => navigate(`/library`)}
                  title={`${t.type}:${t.name} (${t.gallery_count})`}
                >
                  {t.name}
                  <span className="ml-1 opacity-50">{t.gallery_count}</span>
                </button>
                <span className="flex gap-1">
                  <button
                    className={`px-1 ${isIncluded ? "text-emerald-300" : "text-slate-500 hover:text-emerald-300"}`}
                    title="include"
                    onClick={() => toggleFilter(t, "include")}
                  >
                    +
                  </button>
                  <button
                    className={`px-1 ${isExcluded ? "text-rose-300" : "text-slate-500 hover:text-rose-300"}`}
                    title="exclude"
                    onClick={() => toggleFilter(t, "exclude")}
                  >
                    −
                  </button>
                </span>
              </div>
            );
          })}
          {!facetTags.length && (
            <div className="text-xs text-slate-600">no tags yet — add galleries first</div>
          )}
        </div>

        <div className="border-t border-[#222734] pt-2">
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
            filters
          </div>
          <div className="space-y-1 text-xs">
            <button
              className={`btn btn-ghost w-full justify-start text-xs ${favorite ? "text-amber-300" : ""}`}
              onClick={() => setFavorite(favorite ? null : true)}
            >
              ★ favorites only
            </button>
            <button
              className={`btn btn-ghost w-full justify-start text-xs ${downloaded ? "text-emerald-300" : ""}`}
              onClick={() => setDownloaded(downloaded ? null : true)}
            >
              ⬇ downloaded only
            </button>
            <select
              className="w-full"
              value={ratingMin ?? ""}
              onChange={(e) => setRatingMin(e.target.value ? +e.target.value : null)}
            >
              <option value="">any rating</option>
              {[1, 2, 3, 4, 5].map((r) => (
                <option key={r} value={r}>
                  ★ ≥ {r}
                </option>
              ))}
            </select>
            <button className="btn btn-ghost w-full justify-start text-xs" onClick={importLib}>
              ⤴ import folders
            </button>
          </div>
        </div>
      </aside>

      <div className="min-w-0 flex-1 overflow-y-auto p-4">
        {/* search + pills */}
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <input
            className="min-w-64 flex-1"
            placeholder="search titles + tag names…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <select value={sort} onChange={(e) => setSort(e.target.value)} className="w-32">
            {[
              ["added", "recently added"],
              ["released", "release date"],
              ["title", "title"],
              ["rating", "rating"],
              ["pages", "pages"],
              ["progress", "last read"],
            ].map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
          <button className="btn btn-ghost text-xs" onClick={selectAll}>
            {selected.size === items.length && items.length ? "clear selection" : "select all"}
          </button>
        </div>

        {(include.length > 0 || exclude.length > 0) && (
          <div className="mb-3 flex flex-wrap items-center gap-1.5">
            <span className="text-xs text-slate-500">filters:</span>
            {include.map((t) => (
              <TagPill
                key={`i:${t.type}:${t.name}`}
                tag={{ ...t, id: `i:${t.name}` }}
                prefix="include"
                removable
                onRemove={(tag) =>
                  setInclude(include.filter((x) => !(x.type === tag.type && x.name === tag.name)))
                }
              />
            ))}
            {exclude.map((t) => (
              <TagPill
                key={`e:${t.type}:${t.name}`}
                tag={{ ...t, id: `e:${t.name}` }}
                prefix="exclude"
                removable
                onRemove={(tag) =>
                  setExclude(exclude.filter((x) => !(x.type === tag.type && x.name === tag.name)))
                }
              />
            ))}
            <button
              className="btn btn-ghost text-xs"
              onClick={() => {
                setInclude([]);
                setExclude([]);
              }}
            >
              clear all
            </button>
          </div>
        )}

        {/* bulk bar */}
        {selected.size > 0 && (
          <div className="mb-3 flex flex-wrap items-center gap-2 rounded-md bg-indigo-950/60 p-2 text-sm">
            <span className="text-xs text-indigo-200">{selected.size} selected</span>
            <button className="btn btn-primary text-xs" onClick={() => bulk("download")}>
              ⬇ download
            </button>
            <select
              className="w-40"
              defaultValue=""
              onChange={(e) => e.target.value && bulk("collection", e.target.value)}
            >
              <option value="">add to collection…</option>
              {collections.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
            <button className="btn btn-ghost text-xs" onClick={() => bulk("favorite", true)}>
              ★ favorite
            </button>
            <button className="btn btn-danger text-xs" onClick={() => bulk("delete")}>
              delete
            </button>
            <button className="btn btn-ghost text-xs" onClick={() => setSelected(new Set())}>
              cancel
            </button>
          </div>
        )}

        {error && <div className="mb-3 text-sm text-rose-400">{error}</div>}

        <InfiniteScroll
          hasMore={offset + items.length < total}
          onLoadMore={() => {
            if (!loading) {
              const next = offset + 60;
              setOffset(next);
              load(next, false);
            }
          }}
        >
          <GalleryGrid
            items={items.map((g) => ({ ...g, selected: selected.has(g.id) }))}
            empty={loading ? "" : "library empty — browse and add galleries, or import folders"}
            actions={(g) => (
              <>
                <button
                  className={`btn text-xs ${selected.has(g.id) ? "btn-primary" : "btn-ghost"}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    toggleSelect(g.id);
                  }}
                >
                  {selected.has(g.id) ? "✓ selected" : "select"}
                </button>
                {g.download_status !== "done" && (
                  <button
                    className="btn btn-ghost text-xs"
                    onClick={async (e) => {
                      e.stopPropagation();
                      await api.post("/api/downloads", { id: g.id });
                      load(0, true);
                    }}
                  >
                    ⬇
                  </button>
                )}
                <button
                  className="btn btn-ghost text-xs"
                  onClick={(e) => {
                    e.stopPropagation();
                    navigate(`/reader/${g.id}`);
                  }}
                >
                  ▶
                </button>
              </>
            )}
          />
        </InfiniteScroll>
        <div className="mt-3 text-xs text-slate-500">
          {items.length} shown · {total} total
        </div>
      </div>
    </div>
  );
}
