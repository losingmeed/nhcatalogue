import { useEffect, useState } from "react";
import { api, mediaUrl, pageUrl } from "../api.js";
import { TagPill } from "../components/GalleryBits.jsx";
import AsyncImage from "../components/AsyncImage.jsx";
import { navigate } from "../App.jsx";

// Gallery detail: cover, full clickable tag list, actions —
// Add to catalogue · Download · Read · Open folder (plan §6.2).
export default function GalleryDetail({ id }) {
  const [g, setG] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = async () => {
    try {
      setG(await api.get(`/api/galleries/${id}`));
    } catch (e) {
      setError(e.message);
    }
  };
  useEffect(() => {
    setG(null);
    setError("");
    load();
  }, [id]);

  if (error) return <div className="p-6 text-sm text-rose-400">{error}</div>;
  if (!g) return <div className="p-6 text-slate-500">loading…</div>;

  // previews are marked in_catalogue for the reader, but only a persisted row
  // has user data (favorite/rating/notes) and survives a restart.
  const isPersisted = g.persisted !== false;

  const addToCatalogue = async () => {
    setBusy("add");
    try {
      const row = await api.post("/api/catalogue", { id: g.id, persist: true });
      setG({ ...row, pages: g.pages });
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy("");
    }
  };

  const download = async () => {
    setBusy("download");
    try {
      if (!g.in_catalogue) await addToCatalogue();
      await api.post("/api/downloads", { id: g.id });
      navigate("/downloads");
    } catch (e) {
      alert(`download failed: ${e.message}`);
    } finally {
      setBusy("");
    }
  };

  const openFolder = async () => {
    try {
      const q = await api.get(`/api/downloads`);
      const mine = q.items.find((x) => x.gallery_id === g.id);
      if (mine?.folder_path) {
        // no shell access from the browser; the tray/desktop menu provides
        // "open library folder". We show the path instead.
        await navigator.clipboard?.writeText(mine.folder_path);
        alert(`Folder path (copied):\n${mine.folder_path}`);
      } else {
        alert("not downloaded yet");
      }
    } catch (e) {
      alert(e.message);
    }
  };

  const searchTag = (tag) => {
    window.location.hash = `#/browse?q=${encodeURIComponent(`${tag.type}:${tag.name}`)}`;
  };

  const tagsByType = {};
  (g.tags || []).forEach((t) => {
    tagsByType[t.type] = tagsByType[t.type] || [];
    tagsByType[t.type].push(t);
  });
  const TYPE_LABEL = {
    artist: "Artist",
    parody: "Parody",
    character: "Characters",
    group: "Groups",
    language: "Language",
    category: "Category",
    general: "Tags",
  };

  return (
    <div className="p-6">
      <div className="flex flex-wrap gap-6">
        <AsyncImage
          src={mediaUrl(g.cover_url || `/api/cover/${g.id}`)}
          alt={g.title}
          eager
          wrapperClassName="aspect-[5/7] w-56 rounded-lg border border-line"
          className="h-full w-full object-cover"
        />
        <div className="min-w-72 flex-1">
          <h1 className="text-xl font-semibold">{g.title}</h1>
          <div className="mt-1 text-sm text-slate-400">
            #{g.id} · {g.num_pages || (g.pages || []).length} pages
            {g.language && ` · ${g.language}`}
            {g.upload_date && ` · ${new Date(g.upload_date * 1000).toLocaleDateString()}`}
          </div>

          <div className="mt-4 flex flex-wrap gap-2">
            {g.in_catalogue ? (
              <>
                <span className="btn btn-ghost cursor-default">
                  {isPersisted ? "✓ in catalogue" : "👁 preview (not saved)"}
                </span>
                {!isPersisted && (
                  <button className="btn btn-primary" disabled={busy} onClick={addToCatalogue}>
                    {busy === "add" ? "saving…" : "💾 Save to catalogue"}
                  </button>
                )}
                {g.download_status === "done" ? (
                  <span className="btn btn-ghost cursor-default">⬇ downloaded</span>
                ) : (
                  <button className="btn btn-primary" disabled={busy} onClick={download}>
                    {busy === "download" ? "queueing…" : "⬇ Download"}
                  </button>
                )}
              </>
            ) : (
              <button className="btn btn-primary" disabled={busy} onClick={addToCatalogue}>
                {busy === "add" ? "adding…" : "+ Add to catalogue"}
              </button>
            )}
            {g.in_catalogue && (
              <button
                className="btn btn-primary"
                onClick={() => navigate(`/reader/${g.id}`)}
              >
                ▶ Read
              </button>
            )}
            <button className="btn btn-ghost" onClick={openFolder}>
              📂 Open folder
            </button>
            {isPersisted && (
              <>
                <button
                  className="btn btn-ghost"
                  onClick={async () => {
                    await api.patch(`/api/catalogue/${g.id}`, { favorite: !g.favorite });
                    load();
                  }}
                >
                  {g.favorite ? "★ favorited" : "☆ favorite"}
                </button>
                <select
                  className="w-24"
                  value={g.rating || 0}
                  onChange={async (e) => {
                    await api.patch(`/api/catalogue/${g.id}`, { rating: +e.target.value });
                    load();
                  }}
                >
                  {[0, 1, 2, 3, 4, 5].map((r) => (
                    <option key={r} value={r}>
                      {r ? "★".repeat(r) : "rate"}
                    </option>
                  ))}
                </select>
                <button
                  className="btn btn-danger"
                  onClick={async () => {
                    if (!confirm(`remove #${g.id} from catalogue?`)) return;
                    const del = confirm("also delete downloaded files?");
                    await api.del(`/api/catalogue/${g.id}`, { delete_files: del });
                    navigate("/library");
                  }}
                >
                  remove
                </button>
              </>
            )}
          </div>

          {isPersisted && g.notes !== undefined && (
            <div className="mt-4">
              <textarea
                className="w-full max-w-xl text-xs"
                rows={2}
                placeholder="private notes…"
                defaultValue={g.notes}
                onBlur={async (e) => {
                  if (e.target.value !== g.notes)
                    await api.patch(`/api/catalogue/${g.id}`, { notes: e.target.value });
                }}
              />
            </div>
          )}

          <div className="mt-6 space-y-3">
            {Object.entries(tagsByType).map(([type, tags]) => (
              <div key={type}>
                <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  {TYPE_LABEL[type] || type}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {tags.map((t) => (
                    <TagPill key={t.id} tag={t} onClick={searchTag} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {(g.pages || []).length > 0 && (
        <div className="mt-8">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            preview
          </div>
          <div className="flex gap-2 overflow-x-auto pb-2">
            {g.pages.slice(0, 12).map((p) => (
              <AsyncImage
                key={p.n}
                src={pageUrl(g.id, p.n)}
                alt={`page ${p.n}`}
                eager
                wrapperClassName="h-48 min-w-32 shrink-0 cursor-pointer overflow-hidden rounded border border-line bg-panel-deep"
                className="h-full w-auto object-contain"
                onClick={() => navigate(`/reader/${g.id}?page=${p.n}`)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
