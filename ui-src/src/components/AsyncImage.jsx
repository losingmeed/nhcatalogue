import { useEffect, useRef, useState } from "react";

// Image with a skeleton while loading, a retryable error state, and a fade-in
// once decoded. The wrapper must have a size (aspect ratio / height) so the
// placeholder does not collapse and the layout stays stable.
export default function AsyncImage({
  src,
  alt = "",
  className = "",
  wrapperClassName = "",
  eager = false,
  loading = "lazy",
  onClick,
  title,
  autoRetry = 2, // Big Pickle Phase 3: auto-retry failed page loads (client
                 // already retries 429/500 with backoff; this is a UI-level retry
                 // for transient reader/CDN blips).
}) {
  const [status, setStatus] = useState("loading");
  const [attempt, setAttempt] = useState(0);
  const [visible, setVisible] = useState(eager);
  const boxRef = useRef(null);
  const imgRef = useRef(null);
  const retryTimer = useRef(null);

  useEffect(() => {
    setStatus("loading");
    setAttempt(0);
    setVisible(eager);
  }, [src, eager]);

  useEffect(() => {
    if (visible) return;
    const el = boxRef.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true);
          io.disconnect();
        }
      },
      { rootMargin: "600px" }
    );
    io.observe(el);
    return () => io.disconnect();
  }, [visible]);

  const retrySrc = attempt
    ? `${src}${src.includes("?") ? "&" : "?"}retry=${attempt}`
    : src;

  // cached images can finish before React attaches onLoad
  useEffect(() => {
    const img = imgRef.current;
    if (img?.complete && img.naturalWidth > 0) setStatus("loaded");
  }, [retrySrc, visible]);

  // auto-retry a failed load after a short delay (bounded by autoRetry)
  useEffect(() => {
    if (status !== "error" || attempt >= autoRetry) return;
    retryTimer.current = setTimeout(() => {
      setStatus("loading");
      setAttempt((a) => a + 1);
    }, 1500);
    return () => clearTimeout(retryTimer.current);
  }, [status, attempt, autoRetry]);

  return (
    <div
      ref={boxRef}
      className={`relative ${wrapperClassName}`}
      onClick={onClick}
      title={title}
    >
      {status === "loading" && <div className="skeleton absolute inset-0" />}
      {status === "error" ? (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-1.5 p-2 text-center text-[11px] text-slate-500">
          <span aria-hidden="true" className="text-lg opacity-50">
            ▧
          </span>
          <span className="line-clamp-2 break-words">
            {alt ? `${alt} failed` : "image failed"}
          </span>
          <button
            type="button"
            className="btn btn-ghost !px-2 !py-0.5 text-[11px]"
            onClick={(e) => {
              e.stopPropagation();
              setStatus("loading");
              setAttempt((a) => a + 1);
            }}
          >
            retry
          </button>
        </div>
      ) : (
        visible && (
          <img
            ref={imgRef}
            src={retrySrc}
            alt={alt}
            loading={loading}
            decoding="async"
            className={`${className} transition-opacity duration-300 ${
              status === "loaded" ? "opacity-100" : "opacity-0"
            }`}
            onLoad={() => setStatus("loaded")}
            onError={() => setStatus("error")}
          />
        )
      )}
    </div>
  );
}
