"""nhcatalogue entry point: desktop shell + CLI (plan §7).

``python -m nhcatalogue`` with no arguments launches the desktop app (pywebview
window over the local FastAPI server). Subcommands run headless:

  serve                  start the API + UI server only (browser/CLI mode)
  search <query>         live search, print results as a table
  add <id> [<id>…]       add galleries to the catalogue (fetch detail + tags)
  download <id>          add + download a gallery into the folder library
  import <path>          scan an existing folder tree into the catalogue
  sync                   run all watchlists once now
  status                 show settings/domain health summary
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from . import __version__
from .app import App


def _print_gallery(g: dict) -> None:
    tags = ", ".join(f"{t['type']}:{t['name']}" for t in g.get("tags", [])[:8])
    print(f"  #{g['id']:<8} {g.get('title_pretty') or g.get('title_en') or g['id']}")
    print(f"     pages={g.get('num_pages', '?')}  lang={g.get('language') or '?'}  {tags}")


async def cmd_search(app: App, args) -> int:
    async with app.client as client:
        app.client._client = client  # keep handle for close
        try:
            result = await client.search(args.query, page=args.page, sort=args.sort)
        except Exception as e:
            print(f"search failed: {e}", file=sys.stderr)
            return 1
    print(f"page {result.page}/{result.total_pages or '?'}  ({len(result.galleries)} results)")
    for g in result.galleries[: args.limit]:
        print(f"  #{g.id:<8} {g.title}")
        tag_bits = ", ".join(f"{t.type}:{t.name}" for t in g.tags[: args.tags])
        if tag_bits:
            print(f"            {tag_bits}")
    return 0


async def cmd_add(app: App, args) -> int:
    client = app.client
    rc = 0
    for gid in args.ids:
        try:
            gallery = await client.gallery(int(gid))
        except Exception as e:
            print(f"add {gid}: failed ({e})", file=sys.stderr)
            rc = 1
            continue
        await app.db.upsert_gallery(gallery)
        # cache the cover so the catalogue grid works offline
        try:
            cover = await client.fetch_image(client.cover_url(gallery))
            path = app.covers_dir / f"{gallery.id}.jpg"
            path.write_bytes(cover)
            await app.db.update_user_data(gallery.id, {"cover_path": str(path)})
        except Exception as e:
            print(f"  (cover fetch failed: {e})", file=sys.stderr)
        g = await app.db.get_gallery(gallery.id)
        _print_gallery(g)
        print(f"  → added to catalogue #{gallery.id}")
    return rc


async def cmd_download(app: App, args) -> int:
    rc = await cmd_add(app, args)
    if rc:
        return rc
    dl = app.downloader
    for gid in args.ids:
        qid = await dl.enqueue(int(gid))
        if qid is None:
            print(f"download {gid}: not queued", file=sys.stderr)
            rc = 1
    # run until the queue drains, then stop
    try:
        while True:
            await asyncio.sleep(0.5)
            items = await app.db.queue_list()
            active = [q for q in items if q["status"] in ("queued", "running", "paused")]
            if not active:
                break
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        await dl.stop()
    items = await app.db.queue_list()
    for q in items[: len(args.ids)]:
        mark = "✓" if q["status"] == "done" else "✗"
        print(f"{mark} {q['title_pretty'] or q['gallery_id']}: {q['status']} "
              f"{q['pages_done']}/{q['pages_total']}")
        if q["error"]:
            print(f"   {q['error']}")
        if q["status"] == "done":
            print(f"   → {q['folder_path']}")
    return 0 if all(q["status"] == "done" for q in items[: len(args.ids)]) else 1


async def cmd_import(app: App, args) -> int:
    from .downloader import import_library

    root = Path(args.path).expanduser()
    ids = await import_library(app.db, root, client=app.client, fetch_missing_detail=args.fetch)
    print(f"imported {len(ids)} galleries from {root}")
    for gid in ids:
        g = await app.db.get_gallery(gid)
        if g:
            print(f"  #{gid:<8} {g.get('title_pretty') or gid}  → {g.get('folder_path')}")
    return 0


async def cmd_sync(app: App, args) -> int:
    results = await app.syncer.sync_all()
    if not results:
        print("no watchlists configured")
        return 0
    for wid, res in results.items():
        if "error" in res:
            print(f"watchlist {wid}: error — {res['error']}")
        else:
            print(f"watchlist {wid}: {res.get('count', 0)} new galleries")
    return 0


async def cmd_serve(app: App, args) -> int:
    from .server import run_server

    app.save_settings()
    await run_server(app, host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


async def cmd_status(app: App, args) -> int:
    s = app.settings
    print(f"nhcatalogue {__version__}")
    print(f"  data dir     {app.data_dir}")
    print(f"  library root {app.resolved_library_root}")
    print(f"  template    {s.folder_template}")
    print(f"  domains     {', '.join(s.domains)}")
    print(f"  proxy       {s.proxy or '(none)'}")
    print(f"  cf_clearance {'set (' + str(len(s.cf_clearance)) + ' chars)' if s.cf_clearance else '(none)'}")
    print(f"  flaresolverr {s.flaresolverr_url or '(none)'}")
    print(f"  rate limit   {s.rate_limit_rps} rps +-{s.rate_limit_jitter} jitter")
    from .client import HAS_CURL_CFFI

    print(f"  transport    {'curl_cffi (chrome impersonation)' if HAS_CURL_CFFI else 'httpx (fallback)'}")
    n = await app.db.count_galleries()
    print(f"  catalogue    {n} galleries")
    if args.test:
        print("  testing connection…")
        res = await app.client.test_connection()
        print(f"  → {'OK' if res['ok'] else 'BLOCKED'}: {res['message']}")
        return 0 if res["ok"] else 1
    return 0


async def cmd_gui(app: App, args) -> int:
    """Desktop mode: free port → uvicorn → pywebview window (+ tray)."""
    from .desktop import run_desktop

    app.save_settings()
    await run_desktop(app, no_tray=args.no_tray)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nhcatalogue", description="local nhentai catalogue / downloader"
    )
    p.add_argument("--version", action="version", version=f"nhcatalogue {__version__}")
    p.add_argument("--data-dir", default=None, help="override data directory")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("search", help="live search")
    sp.add_argument("query")
    sp.add_argument("--page", type=int, default=1)
    sp.add_argument("--sort", default="", help="date|popular|popular-month")
    sp.add_argument("--limit", type=int, default=25)
    sp.add_argument("--tags", type=int, default=6, help="tags shown per gallery")

    sp = sub.add_parser("add", help="add galleries to the catalogue")
    sp.add_argument("ids", nargs="+")

    sp = sub.add_parser("download", help="add + download galleries")
    sp.add_argument("ids", nargs="+")

    sp = sub.add_parser("import", help="import an existing folder library")
    sp.add_argument("path")
    sp.add_argument("--fetch", action="store_true", help="fetch missing details from the API")

    sp = sub.add_parser("sync", help="run all watchlists now")

    sp = sub.add_parser("serve", help="run the server (headless)")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=0, help="0 = free port")
    sp.add_argument("--no-browser", action="store_true")

    sp = sub.add_parser("status", help="show settings summary")
    sp.add_argument("--test", action="store_true", help="also test the connection")

    sp = sub.add_parser("gui", help="launch the desktop window (default)")
    sp.add_argument("--no-tray", action="store_true")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd is None:
        args.cmd = "gui"
    app = App(args.data_dir)
    commands = {
        "search": cmd_search,
        "add": cmd_add,
        "download": cmd_download,
        "import": cmd_import,
        "sync": cmd_sync,
        "serve": cmd_serve,
        "status": cmd_status,
        "gui": cmd_gui,
    }
    fn = commands[args.cmd]
    try:
        return asyncio.run(_run_and_close(fn, app, args))
    except KeyboardInterrupt:  # pragma: no cover
        return 130


async def _run_and_close(fn, app: App, args) -> int:
    try:
        return await fn(app, args)
    finally:
        app.close()


if __name__ == "__main__":
    sys.exit(main())
