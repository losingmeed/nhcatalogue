import { useState } from "react";
import { api } from "../api.js";

// First-run wizard (plan §6.10): 18+ confirmation, library location,
// default language filter.
export default function FirstRun({ settings, onDone }) {
  const [step, setStep] = useState(1);
  const [libraryRoot, setLibraryRoot] = useState(settings.library_root || "");
  const [language, setLanguage] = useState(settings.default_language || "");
  const [busy, setBusy] = useState(false);

  const finish = async () => {
    setBusy(true);
    try {
      await api.put("/api/settings", {
        patch: {
          library_root: libraryRoot,
          default_language: language,
          age_confirmed: true,
          first_run_done: true,
        },
      });
      await onDone?.();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="card w-full max-w-lg p-8">
        {step === 1 && (
          <>
            <h1 className="text-xl font-bold">nhcatalogue</h1>
            <p className="mt-2 text-sm text-slate-400">
              A local catalogue and downloader for nhentai.net. Everything stays on
              this machine; nothing is uploaded anywhere.
            </p>
            <div className="my-6 rounded-md border border-amber-800/50 bg-amber-950/30 p-4 text-sm">
              <p className="font-semibold text-amber-200">Adult content — 18+ only</p>
              <p className="mt-1 text-xs text-amber-200/70">
                This tool accesses an adult manga site. Confirm you are of legal age
                where you live and that viewing such material is legal for you. You
                are responsible for what you choose to store on your disk.
              </p>
            </div>
            <div className="flex justify-end gap-2">
              <button className="btn btn-primary" onClick={() => setStep(2)}>
                I am 18 or older — continue
              </button>
            </div>
          </>
        )}

        {step === 2 && (
          <>
            <h1 className="text-lg font-semibold">Where should the library live?</h1>
            <p className="mt-2 text-xs text-slate-500">
              Downloaded galleries go into folders here, templated as
              <code className="mx-1">{settings.folder_template || "{artist}/{id} - {title}"}</code>
              under the root.
            </p>
            <input
              className="mt-4 w-full"
              value={libraryRoot}
              onChange={(e) => setLibraryRoot(e.target.value)}
              placeholder="/home/you/library"
            />
            <p className="mt-2 text-[11px] text-slate-600">
              Existing folder libraries can be imported later from the Library view.
            </p>
            <div className="mt-6 flex justify-between">
              <button className="btn btn-ghost" onClick={() => setStep(1)}>
                ← back
              </button>
              <button className="btn btn-primary" onClick={() => setStep(3)}>
                next →
              </button>
            </div>
          </>
        )}

        {step === 3 && (
          <>
            <h1 className="text-lg font-semibold">Default language filter</h1>
            <p className="mt-2 text-xs text-slate-500">
              Applied to new watchlists as a starting point (you can always change it).
              Leave empty for everything.
            </p>
            <select className="mt-4 w-full" value={language} onChange={(e) => setLanguage(e.target.value)}>
              <option value="">no filter — all languages</option>
              {["english", "japanese", "chinese", "korean", "spanish", "french", "german", "russian", "portuguese", "italian", "thai", "vietnamese", "polish", "hungarian", "dutch", "czech", "indonesian", "arabic", "turkish", "finnish", "swedish", "danish", "norwegian", "greek", "hebrew", "hindi", "mongolian", "persian", "ukrainian", "bengali", "burmese", "cebuano", "esperanto", "kannada", "latvian", "n/a"].map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
            <div className="mt-6 flex justify-between">
              <button className="btn btn-ghost" onClick={() => setStep(2)}>
                ← back
              </button>
              <button className="btn btn-primary" disabled={busy} onClick={finish}>
                {busy ? "saving…" : "finish — open the app"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
