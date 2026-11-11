import { useCallback, useEffect, useMemo, useState } from "react";
import { api, mediaUrl } from "../api.js";
import LargeFileWarning, { formatBytes } from "../components/LargeFileWarning.jsx";

export default function Rule34Viewer({ items, index, onIndexChange, onClose }) {
  const [post, setPost] = useState(null);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [warn, setWarn] = useState(null);
  const [queueing, setQueueing] = useState(false);

  const current = items[index];
  const galleryId = current?.id;

  const load = useCallback(async () => {
    if (!galleryId) return;
    setLoading(true);
    setError("");
    setPage(1);
    try {
      const data = await api.get(`/api/rule34/post/${galleryId}`);
      setPost(data);
    } catch (e) {
      setError(e.message);
      setPost(null);
    } finally {
      setLoading(false);
    }
  }, [galleryId]);

  useEffect(() => {
    load();
  }, [load]);

  const total = items.length;
  const go = useCallback(
    (delta) => {
      const next = index + delta;
      if (next < 0 || next >= total) return;
      onIndexChange(next);
    },
    [index, total, onIndexChange]
  );

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "ArrowRight") go(1);
      else if (e.key === "ArrowLeft") go(-1);
      else if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, onClose]);

  const pages = post?.pages || [];
  const isVideo = post?.kind === "video";
  const mediaSrc = useMemo(() => {
    if (!post || !pages.length) return "";
    const n = Math.min(page, pages.length);
    return mediaUrl(`/api/rule34/media/${post.id}?n=${n}`);
  }, [post, page, pages.length]);

  const startDownload = useCallback(async () => {
    if (!post) return;
    setQueueing(true);
    try {
      await api.post("/api/downloads", { id: post.id, persist: true });
      setWarn(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setQueueing(false);
    }
  }, [post]);

  const onDownloadClick = useCallback(() => {
    if (!post) return;
    if (post.large_file) {
      setWarn({
        title: post.title,
        size: post.file_size,
        threshold: post.large_file_threshold,
      });
    } else {
      startDownload();
    }
  }, [post, startDownload]);

  return (
    <div className="flex h-full flex-col bg-black/40">
      <div className="flex items-center justify-between border-b border-line bg-surface px-4 py-2">
        <div className="min-w-0">
          <div className="truncate text-sm text-slate-200">
            {post?.title || current?.title || "…"}
          </div>
          <div className="text-[11px] text-slate-500">
            {post ? `post ${post.post_id}` : ""}
            {post?.kind === "video" ? " · video" : ""}
            {post?.file_size ? ` · ${formatBytes(post.file_size)}` : ""}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn btn-ghost text-xs" onClick={onDownloadClick} disabled={queueing}>
            {queueing ? "queueing…" : "⬇ download"}
          </button>
          <button className="btn btn-ghost text-xs" onClick={onClose}>
            ✕ close
          </button>
        </div>
      </div>

      {error && (
        <div className="m-4 rounded border border-rose-800 bg-rose-950/60 px-3 py-2 text-sm text-rose-300">
          {error}
        </div>
      )}

      <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto p-3">
        {loading && <div className="text-sm text-slate-400">loading…</div>}
        {!loading && post && pages.length > 0 && (
          <div className="flex max-h-full max-w-full flex-col items-center gap-2">
            {isVideo ? (
              <video
                key={mediaSrc}
                src={mediaSrc}
                controls
                playsInline
                className="max-h-[75vh] max-w-full rounded bg-black"
              />
            ) : (
              <img
                key={mediaSrc}
                src={mediaSrc}
                alt={post.title || ""}
                className="max-h-[75vh] max-w-full rounded object-contain"
              />
            )}
            {pages.length > 1 && (
              <div className="flex items-center gap-2 text-xs text-slate-400">
                <button
                  className="btn btn-ghost text-xs"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page <= 1}
                >
                  ◀ file
                </button>
                <span>
                  {page} / {pages.length}
                </span>
                <button
                  className="btn btn-ghost text-xs"
                  onClick={() => setPage((p) => Math.min(pages.length, p + 1))}
                  disabled={page >= pages.length}
                >
                  file ▶
                </button>
              </div>
            )}
          </div>
        )}
        {!loading && post && pages.length === 0 && (
          <div className="text-sm text-slate-500">no media for this post</div>
        )}
      </div>

      {post?.tags?.length > 0 && (
        <div className="max-h-24 overflow-y-auto border-t border-line bg-surface px-4 py-2">
          <div className="flex flex-wrap gap-1">
            {post.tags.map((t) => (
              <span
                key={`${t.type}:${t.name}`}
                className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-300"
              >
                {t.type !== "general" && <span className="text-slate-500">{t.type}: </span>}
                {t.name}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="flex items-center justify-between border-t border-line bg-surface px-4 py-2">
        <button className="btn btn-ghost" onClick={() => go(-1)} disabled={index <= 0}>
          ◀ back
        </button>
        <span className="text-xs text-slate-500">
          {total ? `${index + 1} / ${total}` : "0 / 0"}
        </span>
        <button className="btn btn-ghost" onClick={() => go(1)} disabled={index >= total - 1}>
          next ▶
        </button>
      </div>

      <LargeFileWarning
        open={!!warn}
        title={warn?.title}
        size={warn?.size}
        threshold={warn?.threshold}
        onCancel={() => setWarn(null)}
        onConfirm={() => {
          setWarn(null);
          startDownload();
        }}
      />
    </div>
  );
}
