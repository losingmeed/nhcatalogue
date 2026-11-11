import { useEffect, useState } from "react";
import { DIALOGUE_POOLS, PERSONALITIES, pickDialogue } from "../goon/dialogues.js";

const COOLDOWN_MS = 60_000; // one press per minute
const COUNT_KEY = "nhc_goon_count";
const LAST_KEY = "nhc_goon_last";

function readStoredInt(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    const n = raw === null ? NaN : parseInt(raw, 10);
    return Number.isFinite(n) ? n : fallback;
  } catch {
    return fallback;
  }
}

// Goon counter + automated praise chatbot. The counter button can be pressed
// once per COOLDOWN_MS; every press delivers a fresh premade praise dialogue
// from a random personality.
export default function GoonCounter() {
  const [count, setCount] = useState(() => readStoredInt(COUNT_KEY, 0));
  const [lastPress, setLastPress] = useState(() => readStoredInt(LAST_KEY, 0));
  const [message, setMessage] = useState("");
  const [personality, setPersonality] = useState("");
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const cooldownLeftMs = Math.max(0, COOLDOWN_MS - (now - lastPress));
  const ready = cooldownLeftMs <= 0;

  const press = () => {
    if (!ready) return;
    const next = count + 1;
    setCount(next);
    const ts = Date.now();
    setLastPress(ts);
    setNow(ts);
    try {
      localStorage.setItem(COUNT_KEY, String(next));
      localStorage.setItem(LAST_KEY, String(ts));
    } catch {
      /* storage unavailable — keep in-memory only */
    }
    const p = PERSONALITIES[Math.floor(Math.random() * PERSONALITIES.length)];
    setPersonality(p);
    setMessage(pickDialogue(p));
  };

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-xl font-semibold">Goon Counter</h1>
        <p className="mt-1 text-sm text-slate-400">
          press the button. the bot will praise you. the bot will not stop.
        </p>
      </div>

      <div className="card flex flex-col items-center p-8">
        <div className="text-6xl font-bold text-indigo-300">{count}</div>
        <div className="mt-2 text-[10px] uppercase tracking-widest text-slate-500">
          goons delivered
        </div>
        <button
          className="btn btn-danger mt-6 text-lg"
          onClick={press}
          disabled={!ready}
        >
          GOON
        </button>
        <div className="mt-4 text-xs text-slate-500">
          {ready ? "press allowed" : `cooldown — ${Math.ceil(cooldownLeftMs / 1000)}s`}
        </div>
      </div>

      {message && (
        <div className="card mt-6 p-5">
          <div className="text-[10px] uppercase tracking-widest text-slate-500">
            {personality} says:
          </div>
          <p className="mt-3 text-sm leading-6 text-slate-200">{message}</p>
        </div>
      )}

      {Object.keys(DIALOGUE_POOLS).length > 0 && (
        <p className="mt-4 text-[11px] text-slate-600">
          {PERSONALITIES.length} premade personalities ·{" "}
          {Object.values(DIALOGUE_POOLS)
            .map((p) => p.length)
            .reduce((a, b) => a + b, 0)}{" "}
          premade dialogues
        </p>
      )}
    </div>
  );
}
