# nhcatalogue

A **local catalogue, browser and downloader for [nhentai.net](https://nhentai.net)** — a personal, local-use metadata browser and downloader (similar to existing open-source tools). Browse through the app, add galleries to your own catalogue, and fully download them into an organized folder library — with built-in handling for ISP/country blocks and Cloudflare challenges.

> ⚠️ **Adult content — 18+ only.** Local-use tool; no accounts, no telemetry. You are responsible for the legality of what you choose to store where you live.

---

## What it does

- **Browse** the live site through the app (full query syntax: `artist:`, `tag:`, `parody:`, `character:`, `group:`, `language:`, `category:`, `-tag` exclusions, quoted phrases), with **tag autocomplete** while you type (`artist:mi` → suggestions).
- **Catalogue** — add galleries (metadata + cover cached locally so the grid works offline), tag them, favorite/rate/note them, organize into collections.
- **Download** into a templated folder library — `library/<artist>/<id> - <title>/001.jpg …` + `cover.jpg` + Houdoku-compatible `metadata.json` — resumable, deduped, import-friendly.
- **Read** — single/double-page, long-strip, RTL toggle, keyboard nav, preloading, remembers your position. Streams pages through the backend proxy or reads from disk.
- **Automate** — watchlists (saved searches) sync in the background, auto-add new galleries, optionally auto-download them.
- **Beat blocks** — domain rotation + browser TLS impersonation + `cf_clearance` injection + FlareSolverr + proxy, in one strategy chain.

## Install

```bash
cd nhcatalogue
python3 -m venv .venv
source .venv/bin/activate

pip install -e .            # core: fastapi uvicorn httpx[socks] pillow
pip install -e ".[tls]"     # curl_cffi — Chrome TLS impersonation (recommended!)
pip install -e ".[desktop]" # pywebview + pystray — native window + tray
pip install -e ".[dev]"     # pytest etc.

# build the UI (needs node + npm):
cd ui-src && npm install && npm run build && cd ..
```

> **curl_cffi note:** if the wheel for your Python version isn't available, the app still works — it silently falls back to httpx (see *Restriction handling* below; you'll rely more on mirrors/cf_clearance/proxy). Python 3.11–3.13 are the safest bet for wheels.

## Run

```bash
# desktop app (pywebview window + tray):
python -m nhcatalogue            # or: nhcatalogue (console script)

# server + UI in your browser (headless / remote box):
python -m nhcatalogue serve      # prints http://127.0.0.1:<port> + token
```

CLI (headless / cron):

```bash
python -m nhcatalogue search "artist:mi language:english"
python -m nhcatalogue add 123456 234567     # add to catalogue (fetches detail + cover)
python -m nhcatalogue download 123456       # add + download into the folder library
python -m nhcatalogue import ~/old-library  # adopt an existing folder tree
python -m nhcatalogue sync                 # run all watchlists once
python -m nhcatalogue status [--test]      # settings summary / connection test
```

The server binds `127.0.0.1` on a free port and requires a random token (`X-NHC-Token` header) — the desktop shell passes it automatically via the launch URL. **LAN exposure is off by default.**

## How restriction handling works

Only the `client/` layer touches the network. Every outbound request runs through this chain (settings-controlled, each layer optional):

1. **Domain/mirror rotation** — user-editable **ordered** domain list. On failure/timeout/challenge the next domain is tried; domains that keep failing are demoted for a cooldown. Defeats ISP/country DNS+HTTP blocks.
2. **Browser TLS impersonation** — requests go through **curl_cffi** with `impersonate="chrome"`: real browser TLS/JA3/HTTP2 fingerprints. Defeats most Cloudflare bot-rule challenges with zero user action. Falls back to httpx if curl_cffi isn't installed.
3. **Manual `cf_clearance` injection** — paste a `cf_clearance` cookie **plus the exact User-Agent string it was issued for** (CF binds the clearance to UA+IP). Settings → access.
4. **FlareSolverr (optional)** — point at a local FlareSolverr instance; on challenge detection the app asks it for fresh clearance cookies automatically.
5. **SOCKS5/HTTP proxy (optional)** — global proxy for all nhentai hosts (your VPN/Tor). This is the designed answer to country-level SNI/IP blocks that rotation can't fix.

When a Cloudflare challenge is detected (403/503 + `cf-mitigated` header or challenge body markers), the client escalates through the chain and, if everything fails, raises a **blocked** state — the UI shows a **"blocked → what to do"** banner with concrete next steps (edit domains, re-paste clearance, configure FlareSolverr, set a proxy). A "test connection" button reports live status.

**Politeness** (always on): ~1 request/sec default with jitter, per-host concurrency cap (3), exponential backoff on retries, mirrors are never hammered. Watchlists pull only increments past a per-watchlist cursor.

## Data layout

```
~/.local/share/nhcatalogue/          # data dir (XDG_DATA_HOME honoured)
├── catalogue.sqlite3                 # SQLite catalogue (WAL, FTS5, migrations)
└── library/                         # the folder library
    └── <artist>/<id> - <title>/     # folder template (configurable)
        ├── 001.jpg 002.jpg …        # zero-padded pages, original extensions
        ├── cover.jpg
        ├── metadata.json             # Houdoku-compatible (portable to other readers)
        └── .nhc.json                 # internal resume manifest (sizes, downloaded set)

~/.local/share/nhcatalogue/covers/   # cover cache for offline catalogue grid
```

**Folder template** — Settings → library. Variables: `{artist} {parody} {character} {language} {id} {title}` (multi-artist → primary artist). Default: `{artist}/{id} - {title}`. Paths are sanitized (illegal chars, reserved names, length caps).

**Import** — scan any folder tree for `metadata.json` (Houdoku) or `^\d+` folder names; existing folders are adopted into the catalogue and never re-downloaded.

## Tag search (headline feature)

- Tag names are **normalized** (`full color` / `full-color` / `full_color` all match) — stored with a `name_norm` column, indexed for type-ahead.
- **Autocomplete** in the search bar suggests catalogue tags as you type (`artist:mi` → artist matches), with local gallery counts.
- **Tags browser** view: all tags grouped by type (Artist / Parody / Character / Group / Language / Category / General) with your gallery counts, type-ahead filter, multi-select → gallery grid.
- **Faceted filtering** in the Library: `+` include / `−` exclude pills per tag, shown as removable chips; facets reflect the current result set.
- FTS5 over **titles + tag names** for instant local search.
- API: `/api/tags?q=&type=`, `/api/tags/facets`, `/api/tags/groups`, `/api/tags/{id}/galleries`, `/api/tags/suggest`.

## Development

```bash
pytest            # 106 tests — all mocked, zero network calls
ruff check src tests   # if installed
cd ui-src && npm run dev   # Vite dev server (proxies /api to :8787)
```

### Tests — what is mocked vs what needs *your* machine

Everything in `pytest` runs against **mocks/fixtures** (canned nhentai JSON, mock transport, temp dirs) — the sandbox never talks to nhentai or its CDNs. Covered with mocks: CF challenge detection, domain rotation/demotion, rate limiter spacing/jitter/concurrency, FlareSolverr escalation, clearance/UA injection, DB schema+migrations+FTS+normalized tags, faceted queries, downloader resume/manifests/templating/sanitization/import, all API routes, CLI commands, SPA serving.

**Needs verification on your machine (real network / real desktop):**

- Live nhentai API shapes and mirror compatibility (search/detail/tagged endpoints, `i/t` CDN paths) — exact param spellings were implemented from the documented public API but not exercised against the live site.
- curl_cffi `impersonate="chrome"` actually passing Cloudflare on your network.
- Real `cf_clearance` + UA paste flow (cookie expiry detection).
- FlareSolverr against a live instance.
- SOCKS/HTTP proxy connectivity.
- pywebview window + tray icon behavior on your Linux desktop (needs a display).
- PyInstaller build (spec included; run `pyinstaller nhcatalogue.spec`).
- Watchlist background timing over hours (logic is unit-tested; the 6h loop is not).

## Project structure

```
nhcatalogue/
├── pyproject.toml
├── nhcatalogue.spec            # PyInstaller (M5)
├── src/nhcatalogue/
│   ├── __main__.py             # CLI + desktop entry
│   ├── app.py                  # App context (settings+db+client+downloader+sync)
│   ├── config.py               # settings schema, defaults, validation
│   ├── desktop.py              # pywebview window + tray, browser fallback
│   ├── client/                 # THE ONLY network code: transport (curl_cffi/httpx),
│   │                           # domain rotation, rate limiter, CF detection,
│   │                           # models/normalization, FlareSolverr
│   ├── db/                     # SQLite + sequential migrations + FTS5 triggers
│   ├── downloader/             # queue/workers, paths/template, manifests, import
│   ├── sync/                   # watchlist background engine
│   ├── server/                 # FastAPI routes + SPA hosting (token-guarded)
│   └── ui/dist/                # built React app (served when present)
├── ui-src/                     # React + Vite + Tailwind source (dark theme)
├── tests/                      # pytest, all mocked
└── scripts/smoke_server.py     # local-only uvicorn smoke check
```

## Legal / usage notes

- Personal, local-use tool. No accounts, no telemetry, content stays on your disk.
- Polite rate limits are on by default; don't raise them carelessly.
- Nothing is auto-downloaded unless you explicitly create a watchlist with auto-download.
- You are responsible for what you store, and for the legality of viewing/storing it where you live.

## Milestone status

- **M1 — Access core (CLI)** ✅ client chain, SQLite+FTS, folder library, `search/add/download/import/sync/serve` CLI.
- **M2 — Server + Browse UI** ✅ FastAPI + React; browse/search/detail; reader streaming via proxy; add-to-catalogue with cover caching.
- **M3 — Library experience** ✅ faceted tag filters + Tags browser, FTS, read-progress/resume, collections, import.
- **M4 — Automation** ✅ watchlist sync engine, downloads manager, settings UI, blocked banner.
- **M5 — Desktop shell** ✅ pywebview + tray entry (browser fallback), PyInstaller spec, first-run wizard. *(Window/tray need verification on a real desktop.)*
- **M6 — Tauri shell** — not started (optional per plan).
