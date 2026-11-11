import { useEffect, useRef, useState } from "react";

const SOURCES = [
  { id: "browse", label: "Nhentai", icon: "📚", hint: "comics" },
  { id: "rule34", label: "Rule 34", icon: "🖼️", hint: "media" },
];

export default function SourceFlyout({ active, onPick }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const activeSource = SOURCES.find((s) => s.id === active) || SOURCES[0];

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm ${
          activeSource.id === active
            ? "bg-indigo-600/30 text-indigo-200"
            : "text-slate-300 hover:bg-slate-700/30"
        }`}
      >
        <span>
          <span className="mr-2">🔍</span>
          Browse
        </span>
        <span className={`text-[10px] text-slate-500 transition-transform ${open ? "rotate-90" : ""}`}>
          ▶
        </span>
      </button>

      {open && (
        <div className="absolute left-2 right-2 top-full z-40 mt-0.5 overflow-hidden rounded-md border border-line-strong bg-panel-alt shadow-xl">
          {SOURCES.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => {
                setOpen(false);
                onPick(s.id);
              }}
              className={`flex w-full items-center justify-between px-3 py-2 text-left text-xs ${
                s.id === active ? "bg-indigo-600/25 text-indigo-100" : "text-slate-300 hover:bg-slate-700/30"
              }`}
            >
              <span>
                <span className="mr-2">{s.icon}</span>
                {s.label}
              </span>
              <span className="text-[10px] text-slate-500">{s.hint}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
