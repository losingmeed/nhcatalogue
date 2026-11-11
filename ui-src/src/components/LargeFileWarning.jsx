export function formatBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n <= 0) return "unknown size";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  const value = n / Math.pow(1024, i);
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export default function LargeFileWarning({ open, title, size, threshold = 10 * 1024 * 1024, onCancel, onConfirm }) {
  if (!open) return null;
  const large = Number(size) > threshold;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="card w-full max-w-md p-5">
        <h2 className="mb-3 text-base font-semibold text-amber-300">
          {large ? "⚠ Large file" : "Download file"}
        </h2>
        <p className="mb-2 text-sm text-slate-300">
          {title || "This file"} is <span className="font-semibold">{formatBytes(size)}</span>.
        </p>
        {large ? (
          <p className="mb-4 text-sm text-rose-300">
            This is over {formatBytes(threshold)}. Downloading it may take a while and use a lot of
            disk space. Are you sure you want to continue?
          </p>
        ) : (
          <p className="mb-4 text-sm text-slate-400">This file is small enough to fetch directly.</p>
        )}
        <div className="flex justify-end gap-2">
          <button className="btn btn-ghost" onClick={onCancel}>
            cancel
          </button>
          <button className={large ? "btn btn-danger" : "btn btn-primary"} onClick={onConfirm}>
            {large ? "download anyway" : "download"}
          </button>
        </div>
      </div>
    </div>
  );
}
