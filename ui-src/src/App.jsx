import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api.js";
import BlockedBanner from "./components/BlockedBanner.jsx";
import Browse from "./views/Browse.jsx";
import GalleryDetail from "./views/GalleryDetail.jsx";
import Reader from "./views/Reader.jsx";
import Library from "./views/Library.jsx";
import TagsBrowser from "./views/TagsBrowser.jsx";
import Collections from "./views/Collections.jsx";
import Watchlists from "./views/Watchlists.jsx";
import Downloads from "./views/Downloads.jsx";
import SettingsView from "./views/Settings.jsx";
import FirstRun from "./views/FirstRun.jsx";

// tiny hash router: #/browse, #/gallery/123, #/reader/123, ...
function useRoute() {
  const [hash, setHash] = useState(() => window.location.hash || "#/browse");
  useEffect(() => {
    const fn = () => setHash(window.location.hash || "#/browse");
    window.addEventListener("hashchange", fn);
    return () => window.removeEventListener("hashchange", fn);
  }, []);
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  return { view: parts[0] || "browse", arg: parts[1], query: parts[2], hash };
}

export function navigate(to) {
  window.location.hash = to;
}

const NAV = [
  ["browse", "Browse", "🔍"],
  ["library", "Library", "📚"],
  ["tags", "Tags", "🏷️"],
  ["collections", "Collections", "📁"],
  ["watchlists", "Watchlists", "🔔"],
  ["downloads", "Downloads", "⬇️"],
  ["settings", "Settings", "⚙️"],
];

export default function App() {
  const route = useRoute();
  const [settings, setSettings] = useState(null);
  const [firstRunNeeded, setFirstRunNeeded] = useState(false);

  const loadSettings = useCallback(async () => {
    try {
      const s = await api.get("/api/settings");
      setSettings(s);
      setFirstRunNeeded(!s.first_run_done || !s.age_confirmed);
      return s;
    } catch (e) {
      setSettings({ error: e.message });
    }
  }, []);

  useEffect(() => {
    loadSettings();
  }, [loadSettings]);

  const view = useMemo(() => {
    switch (route.view) {
      case "gallery":
        return <GalleryDetail id={route.arg} />;
      case "reader":
        return <Reader id={route.arg} />;
      case "library":
        return <Library />;
      case "tags":
        return <TagsBrowser />;
      case "collections":
        return <Collections />;
      case "watchlists":
        return <Watchlists />;
      case "downloads":
        return <Downloads />;
      case "settings":
        return <SettingsView settings={settings} reload={loadSettings} />;
      case "browse":
      default:
        return <Browse />;
    }
  }, [route.view, route.arg, settings, loadSettings]);

  if (settings && settings.error) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="card max-w-md p-6">
          <h1 className="mb-2 text-lg font-semibold">Connection error</h1>
          <p className="text-sm text-slate-400">{settings.error}</p>
          <p className="mt-2 text-xs text-slate-500">
            Make sure the server is running and the token matches (launch via the app,
            or set the token from the URL hash).
          </p>
        </div>
      </div>
    );
  }

  if (!settings) {
    return <div className="flex h-full items-center justify-center text-slate-500">loading…</div>;
  }

  if (firstRunNeeded) {
    return <FirstRun settings={settings} onDone={loadSettings} />;
  }

  const isReader = route.view === "reader";

  return (
    <div className="flex h-full">
      {!isReader && (
        <aside className="flex w-48 shrink-0 flex-col border-r border-[#222734] bg-[#0e1118]">
          <div className="px-4 py-4 text-lg font-bold tracking-wide text-indigo-300">
            nh<span className="text-slate-200">catalogue</span>
          </div>
          <nav className="flex-1 space-y-0.5 px-2">
            {NAV.map(([id, label, icon]) => (
              <a
                key={id}
                href={`#/${id}`}
                className={`block rounded-md px-3 py-2 text-sm ${
                  route.view === id
                    ? "bg-indigo-600/30 text-indigo-200"
                    : "text-slate-300 hover:bg-slate-700/30"
                }`}
              >
                <span className="mr-2">{icon}</span>
                {label}
              </a>
            ))}
          </nav>
          <div className="px-4 py-3 text-[10px] leading-4 text-slate-600">
            local catalogue · 18+
            <br />
            no telemetry
          </div>
        </aside>
      )}
      <main className="min-w-0 flex-1 overflow-y-auto">
        {!isReader && <BlockedBanner settings={settings} reload={loadSettings} />}
        {view}
      </main>
    </div>
  );
}
