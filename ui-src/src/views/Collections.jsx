import { useEffect, useState } from "react";
import { api } from "../api.js";
import { GalleryGrid } from "../components/GalleryBits.jsx";

// Collections: user-curated shelves (plan §6.6).
export default function Collections() {
  const [collections, setCollections] = useState([]);
  const [current, setCurrent] = useState(null);
  const [items, setItems] = useState([]);
  const [newName, setNewName] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const r = await api.get("/api/collections");
      setCollections(r.items || []);
    } catch (e) {
      setError(e.message);
    }
  };
  useEffect(() => {
    load();
  }, []);

  const open = async (c) => {
    setCurrent(c);
    const r = await api.get("/api/catalogue", { collection: c.id, limit: 200 });
    setItems(r.items);
  };

  const create = async () => {
    if (!newName.trim()) return;
    try {
      await api.post("/api/collections", { name: newName.trim() });
      setNewName("");
      load();
    } catch (e) {
      setError(e.message);
    }
  };

  const remove = async (c) => {
    if (!confirm(`delete collection "${c.name}"? (galleries stay in the catalogue)`)) return;
    await api.del(`/api/collections/${c.id}`);
    if (current?.id === c.id) setCurrent(null);
    load();
  };

  return (
    <div className="p-6">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">Collections</h1>
        <input
          className="w-48"
          placeholder="new collection name…"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && create()}
        />
        <button className="btn btn-primary" onClick={create}>
          create
        </button>
      </div>
      {error && <div className="mb-3 text-sm text-rose-400">{error}</div>}

      <div className="mb-6 flex flex-wrap gap-2">
        {collections.map((c) => (
          <div
            key={c.id}
            className={`card flex items-center gap-2 p-2 ${
              current?.id === c.id ? "border-indigo-600/60" : ""
            }`}
          >
            <button className="btn btn-ghost" onClick={() => open(c)}>
              📁 {c.name}
              <span className="text-xs text-slate-500">{c.gallery_count}</span>
            </button>
            <button
              className="text-slate-500 hover:text-rose-400"
              onClick={() => remove(c)}
              title="delete collection"
            >
              ×
            </button>
          </div>
        ))}
        {!collections.length && (
          <div className="text-sm text-slate-500">
            no collections yet — create one, then use the library bulk actions
          </div>
        )}
      </div>

      {current && (
        <>
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
            {current.name} ({items.length})
          </h2>
          <GalleryGrid items={items} empty="empty shelf — add galleries via library bulk select" />
        </>
      )}
    </div>
  );
}
