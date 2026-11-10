import { useState } from "react";
import { navigate } from "../App.jsx";

// Clear "blocked → what to do" banner when the CF chain fails (plan §6.9).
export default function BlockedBanner({ settings, reload }) {
  const [expanded, setExpanded] = useState(false);
  const [testing, setTesting] = useState(false);
  if (!settings?.blocked) return null;
  const strategies = settings.blocked_strategies || [];
  return (
    <div className="border-b border-rose-900/60 bg-rose-950/60 px-6 py-3 text-sm">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-semibold text-rose-200">
          🚫 Blocked — action needed
        </span>
        <span className="text-rose-300/80">
          Every domain in the rotation failed (Cloudflare / ISP block).
        </span>
        <button className="btn btn-ghost" onClick={() => setExpanded(!expanded)}>
          {expanded ? "hide details" : "what to do"}
        </button>
        <button
          className="btn btn-primary"
          disabled={testing}
          onClick={async () => {
            setTesting(true);
            try {
              await fetch("/api/settings/test", {
                method: "POST",
                headers: { "X-NHC-Token": localStorage.getItem("nhc_token") || "" },
              });
            } finally {
              setTesting(false);
              await reload();
            }
          }}
        >
          {testing ? "testing…" : "test connection"}
        </button>
        <a href="#/settings" className="btn btn-ghost" onClick={() => navigate("/settings")}>
          open settings →
        </a>
      </div>
      {expanded && (
        <div className="mt-2 rounded-md bg-black/30 p-3 text-xs leading-5 text-rose-200/70">
          <p className="mb-2 font-semibold text-rose-200">Try these, in order:</p>
          <ol className="ml-4 list-decimal space-y-1">
            <li>Settings → Domains: add or reorder mirrors (rotation list).</li>
            <li>
              Paste a fresh <code className="text-rose-100">cf_clearance</code> cookie{" "}
              <b>plus the exact User-Agent it was issued for</b>.
            </li>
            <li>Point FlareSolverr at a local instance for automatic solving.</li>
            <li>Set a SOCKS5/HTTP proxy (your VPN) — beats country-level blocks.</li>
          </ol>
          {strategies.length > 0 && (
            <details className="mt-2">
              <summary className="cursor-pointer text-rose-300/60">last attempts</summary>
              <pre className="mt-1 whitespace-pre-wrap">{strategies.join("\n")}</pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
