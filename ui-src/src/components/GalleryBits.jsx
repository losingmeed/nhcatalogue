import { navigate } from "../App.jsx";
import { mediaUrl } from "../api.js";
import AsyncImage from "./AsyncImage.jsx";

export function TagPill({ tag, onClick, prefix, onRemove, removable }) {
  const typeClass = {
    artist: "bg-violet-600/25 text-violet-200 border-violet-700/50",
    parody: "bg-amber-600/25 text-amber-200 border-amber-700/50",
    character: "bg-teal-600/25 text-teal-200 border-teal-700/50",
    group: "bg-pink-600/25 text-pink-200 border-pink-700/50",
    language: "bg-sky-600/25 text-sky-200 border-sky-700/50",
    category: "bg-emerald-600/25 text-emerald-200 border-emerald-700/50",
    general: "bg-slate-600/25 text-slate-200 border-slate-500/40",
  }[tag.type] || "bg-slate-600/25 text-slate-200 border-slate-500/40";
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs ${typeClass}`}>
      {prefix && <b className="opacity-80">{prefix === "include" ? "+" : "−"}</b>}
      <button
        className="hover:underline"
        onClick={(e) => {
          e.stopPropagation();
          onClick ? onClick(tag) : navigate(`/library?tag=${tag.type}:${tag.name}`);
        }}
        title={`${tag.type}: ${tag.name}`}
      >
        {tag.type === "general" ? "" : `${tag.type}:`}
        {tag.name}
        {tag.gallery_count !== undefined && tag.gallery_count > 0 && (
          <span className="ml-1 opacity-50">{tag.gallery_count}</span>
        )}
      </button>
      {removable && onRemove && (
        <button
          className="ml-0.5 opacity-60 hover:opacity-100"
          onClick={(e) => {
            e.stopPropagation();
            onRemove(tag);
          }}
          title="remove filter"
        >
          ×
        </button>
      )}
    </span>
  );
}

export function GalleryCard({ gallery, actions, footer }) {
  const g = gallery;
  const dlBadge =
    g.download_status === "done" ? "⬇" : g.download_status === "queued" ? "⏳" : "";
  return (
    <div
      className="card group cursor-pointer transition-transform hover:-translate-y-0.5 hover:border-indigo-600/60"
      onClick={() => navigate(`/gallery/${g.id}`)}
    >
      <div className="relative aspect-[5/7] w-full overflow-hidden bg-[#0f1218]">
        <AsyncImage
          src={mediaUrl(g.cover_url || `/api/cover/${g.id}`)}
          alt={g.title}
          eager
          wrapperClassName="h-full w-full"
          className="h-full w-full object-cover"
        />
        <div className="absolute left-1 top-1 flex gap-1">
          {g.in_catalogue && (
            <span className="rounded bg-indigo-600/90 px-1.5 text-[10px] font-bold text-white">
              {g.persisted === false ? "PREVIEW" : "CAT"}
            </span>
          )}
          {dlBadge && <span className="rounded bg-black/70 px-1.5 text-[10px]">{dlBadge}</span>}
          {g.favorite && <span className="rounded bg-black/70 px-1.5 text-[10px]">★</span>}
        </div>
        {g.read_progress_pages > 0 && (
          <span className="absolute bottom-1 right-1 rounded bg-black/70 px-1.5 text-[10px]">
            {g.read_progress_pages}/{g.num_pages || "?"}
          </span>
        )}
        <div className="absolute bottom-0 left-0 right-0 flex justify-center gap-1 bg-gradient-to-t from-black/80 to-transparent p-1.5 opacity-0 transition-opacity group-hover:opacity-100">
          {actions}
        </div>
      </div>
      <div className="p-2">
        <div className="line-clamp-2 min-h-8 text-xs leading-4 text-slate-200" title={g.title}>
          {g.title}
        </div>
        <div className="mt-1 flex flex-wrap gap-1 text-[10px] text-slate-500">
          <span>#{g.id}</span>
          <span>·</span>
          <span>{g.num_pages || "?"}p</span>
          {g.language && (
            <>
              <span>·</span>
              <span>{g.language}</span>
            </>
          )}
        </div>
        {footer}
      </div>
    </div>
  );
}

export function GalleryGrid({ items, actions, footer, empty }) {
  if (!items?.length) {
    return (
      <div className="p-10 text-center text-sm text-slate-500">{empty || "nothing here yet"}</div>
    );
  }
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
      {items.map((g) => (
        <GalleryCard key={g.id} gallery={g} actions={actions?.(g)} footer={footer?.(g)} />
      ))}
    </div>
  );
}

// infinite scroll sentinel wrapper
export function InfiniteScroll({ onLoadMore, hasMore, children }) {
  return (
    <>
      {children}
      {hasMore && (
        <div
          className="h-16"
          ref={(el) => {
            if (!el) return;
            const io = new IntersectionObserver((entries) => {
              if (entries[0].isIntersecting) onLoadMore?.();
            });
            io.observe(el);
            el._io = io;
            return () => io.disconnect();
          }}
        />
      )}
    </>
  );
}
