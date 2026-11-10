"""Async download queue with workers, resume, cancel/retry and import (plan §4).

The Downloader owns: queue of gallery ids → folders, N async workers (default
3), template-driven folders, page files (zero-padded + original ext), cover,
metadata.json (Houdoku) and .nhc.json (resume manifest). Dedup by gallery id;
import adopts existing folders without redownloading.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..client.models import Gallery, Page, Tag
from . import manifests
from .paths import page_filename, render_template

if TYPE_CHECKING:  # pragma: no cover
    from ..app import App

log = logging.getLogger("nhcatalogue.downloader")


class Downloader:
    def __init__(self, app: App, workers: int | None = None):
        self.app = app
        self.db = app.db
        self.settings = app.settings
        self.workers = max(1, workers or self.settings.download_workers)
        self._queue: asyncio.Queue[int | None] = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._live_workers = 0
        self._next_worker_id = 0
        self._running = False
        self._cancel_requested: set[int] = set()  # gallery ids to cancel
        self._retry_requested: set[int] = set()
        self._active: set[int] = set()  # gallery ids currently queued or downloading (dup prevention)
        self._paused = asyncio.Event()
        self._paused.set()  # set = not paused
        self._wakeup = asyncio.Event()
        self._wakeup.set()

    # ------------------------------------------------------------------ control

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.set_workers(self.settings.download_workers)

    def _spawn_worker(self) -> None:
        index = self._next_worker_id
        self._next_worker_id += 1
        self._live_workers += 1
        self._tasks.append(
            asyncio.create_task(self._worker(index), name=f"dl-worker-{index}")
        )

    def set_workers(self, n: int) -> None:
        """Live-resize the worker pool (called after Settings is saved)."""
        self.workers = max(1, int(n))
        if not self._running:
            return
        while self._live_workers < self.workers:
            self._spawn_worker()

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        self._live_workers = 0
        # wake anything blocked on an empty queue
        for _ in range(max(1, self.workers)):
            self._queue.put_nowait(None)

    async def enqueue(self, gallery_id: int) -> int | None:
        """Add a gallery to the download queue (dedup: one active item each).

        Duplicate prevention: a gallery already queued/running is not re-queued;
        the existing queue row is returned instead.
        """
        gallery_id = int(gallery_id)
        gallery = await self.db.get_gallery(gallery_id)
        if gallery is None:
            log.warning("enqueue: gallery %s not in catalogue (add it first)", gallery_id)
            return None
        qid = await self.db.queue_add(gallery_id, gallery.get("num_pages", 0) or 0)
        if gallery_id in self._active:
            # duplicate prevented: already queued or downloading
            return qid
        await self.db.update_user_data(gallery_id, {"download_status": "queued"})
        self._active.add(gallery_id)
        self.start()
        self._queue.put_nowait(gallery_id)
        return qid

    async def enqueue_many(self, gallery_ids: list[int]) -> list[int]:
        ids = []
        for gid in gallery_ids:
            qid = await self.enqueue(gid)
            if qid:
                ids.append(qid)
        return ids

    def pause(self) -> None:
        self._paused.clear()

    def resume(self) -> None:
        self._paused.set()

    async def cancel(self, gallery_id: int) -> None:
        self._cancel_requested.add(int(gallery_id))

    async def retry(self, gallery_id: int) -> None:
        gallery_id = int(gallery_id)
        q = await self.db.queue_by_gallery(gallery_id)
        if q:
            await self.db.queue_update(q["id"], {"status": "queued", "retries": q["retries"] + 1, "error": ""})
            self._retry_requested.discard(gallery_id)
            await self.db.update_user_data(gallery_id, {"download_status": "queued"})
            if gallery_id not in self._active:
                self._active.add(gallery_id)
                self.start()
                self._queue.put_nowait(gallery_id)

    # ------------------------------------------------------------------- worker

    async def _worker(self, index: int) -> None:
        try:
            while self._running:
                # a shrink request: let the highest-numbered workers retire
                if self._live_workers > self.workers:
                    return
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=2.0)
                except TimeoutError:
                    continue
                except asyncio.CancelledError:
                    return
                if item is None:
                    self._queue.task_done()
                    return
                try:
                    await self._paused.wait()
                    await self._download_item(item)
                except asyncio.CancelledError:
                    return
                except Exception as e:  # pragma: no cover - defensive
                    log.exception("worker %s failed on gallery %s", index, item)
                finally:
                    self._queue.task_done()
                    if item is not None:
                        self._active.discard(int(item))
        finally:
            self._live_workers = max(0, self._live_workers - 1)

    async def _download_item(self, gallery_id: int) -> None:
        if gallery_id in self._cancel_requested:
            self._cancel_requested.discard(gallery_id)
            q = await self.db.queue_by_gallery(gallery_id)
            if q:
                await self.db.queue_update(q["id"], {"status": "canceled"})
            return

        gallery_dict = await self.db.get_gallery(gallery_id)
        if gallery_dict is None:
            log.warning("gallery %s vanished from catalogue", gallery_id)
            return
        gallery = gallery_from_dict(gallery_dict)
        folder = self.gallery_folder(gallery)
        q = await self.db.queue_by_gallery(gallery_id)
        qid = q["id"] if q else None

        total = len(gallery.pages)
        if qid:
            await self.db.queue_update(
                qid,
                {"status": "running", "pages_total": total, "folder_path": str(folder)},
            )

        # Big Pickle Phase 4: up to 3 total attempts with 5s between failures,
        # then mark the item failed with the reason. Manual retry (via the
        # queue action) is kept and starts a fresh 3-attempt run.
        max_attempts = 3
        retry_delay = 5
        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                await self._attempt_download(
                    gallery_id, gallery, folder, qid, total
                )
                return  # success
            except _Canceled:
                return  # _attempt_download already marked canceled/partial
            except Exception as e:
                last_error = e
                log.exception("download attempt %s/%s failed for gallery %s", attempt, max_attempts, gallery_id)
                if attempt < max_attempts:
                    await self.db.queue_update(
                        qid,
                        {
                            "retries": attempt,
                            "error": f"attempt {attempt} failed ({e.__class__.__name__}): {e}; retrying in {retry_delay}s",
                        },
                    )
                    await self.db.update_user_data(gallery_id, {"download_status": "queued"})
                    await asyncio.sleep(retry_delay)
                # else: fall through to final error handling below

        # exhausted all attempts — mark failed with the reason
        if qid and last_error is not None:
            await self.db.queue_update(
                qid,
                {
                    "status": "error",
                    "retries": max_attempts - 1,
                    "error": f"{last_error.__class__.__name__}: {last_error}",
                },
            )
        await self.db.update_user_data(gallery_id, {"download_status": "error"})

    async def _attempt_download(
        self, gallery_id: int, gallery: Gallery, folder: Path, qid: int | None, total: int
    ) -> None:
        """Single download attempt. Raises _Canceled on cancel, Exception on failure."""
        manifest = manifests.read_manifest(folder)
        if manifest is None or manifest.get("gallery_id") != gallery.id:
            manifest = manifests.build_manifest(gallery)

        pages_done = 0
        bytes_done = 0
        pages_by_number = {p.number: p for p in gallery.pages}
        # batch disk + DB writes: every 8 pages (or once for tiny galleries)
        flush_every = max(1, min(8, total or 1))
        page_concurrency = max(1, int(self.settings.download_page_concurrency))

        cover_path = folder / f"cover.{gallery.cover_ext}"
        cover_task = asyncio.create_task(
            self._fetch_cover(gallery, folder, cover_path), name=f"cover-{gallery_id}"
        )

        folder.mkdir(parents=True, exist_ok=True)
        sem = asyncio.Semaphore(page_concurrency)

        async def fetch_page(page: Page) -> tuple[int, bytes, bool]:
            async with sem:
                await self._paused.wait()  # pause support mid-download
                if gallery_id in self._cancel_requested:
                    raise _Canceled()
                if manifests.verify_page(manifest, page.number, folder, page.ext):
                    return page.number, b"", False  # resume: already on disk
                url = self.app.client.page_url(gallery, page.number)
                data = await self.app.client.fetch_image(url)
                return page.number, data, True

        tasks = [asyncio.create_task(fetch_page(p)) for p in gallery.pages]
        pending: set[asyncio.Task] = set(tasks)
        since_flush = 0
        try:
            try:
                while pending:
                    if gallery_id in self._cancel_requested:
                        raise _Canceled()
                    done, pending = await asyncio.wait(
                        pending, timeout=1.0, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in done:
                        number, data, fetched = task.result()
                        page = pages_by_number.get(number)
                        if page is None:  # pragma: no cover - defensive
                            continue
                        if fetched:
                            dest = folder / page_filename(number, page.ext, total)
                            dest.write_bytes(data)
                            manifests.mark_page_downloaded(manifest, number, len(data))
                            bytes_done += len(data)
                        pages_done += 1
                        since_flush += 1
                    if since_flush >= flush_every:
                        await self._flush_progress(
                            qid, folder, manifest, pages_done, total, bytes_done
                        )
                        since_flush = 0
            finally:
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

            try:
                await cover_task
            except Exception as e:  # cover is optional — never fail the download
                log.debug("cover failed for %s: %s", gallery_id, e)

            manifest["complete"] = True
            await self._flush_progress(
                qid, folder, manifest, pages_done, total, bytes_done
            )
            try:
                await asyncio.to_thread(manifests.write_metadata, folder, gallery)
            except OSError as e:  # pragma: no cover - defensive
                log.debug("metadata write failed for %s: %s", gallery_id, e)
            if qid:
                await self.db.queue_update(
                    qid,
                    {
                        "status": "done",
                        "progress": 100.0,
                        "pages_done": total,
                        "bytes_done": bytes_done,
                        "folder_path": str(folder),
                    },
                )
            await self.db.update_user_data(
                gallery_id,
                {
                    "download_status": "done",
                    "folder_path": str(folder),
                    "cover_path": str(cover_path) if cover_path.exists() else "",
                },
            )
            # the library folder is now authoritative; no duplicate cache
            self.app.page_cache.purge(gallery_id)
        except _Canceled:
            cover_task.cancel()
            await asyncio.gather(cover_task, return_exceptions=True)
            manifest["complete"] = False
            await self._flush_progress(
                qid, folder, manifest, pages_done, total, bytes_done
            )
            if qid:
                await self.db.queue_update(
                    qid,
                    {
                        "status": "canceled",
                        "pages_done": pages_done,
                        "bytes_done": bytes_done,
                    },
                )
            await self.db.update_user_data(gallery_id, {"download_status": "partial"})
            self._cancel_requested.discard(gallery_id)
            raise
        except Exception as e:
            cover_task.cancel()
            await asyncio.gather(cover_task, return_exceptions=True)
            await self._flush_progress(
                qid, folder, manifest, pages_done, total, bytes_done
            )
            if qid:
                await self.db.queue_update(
                    qid, {"status": "error", "error": f"{e.__class__.__name__}: {e}"}
                )
            await self.db.update_user_data(gallery_id, {"download_status": "error"})
            raise

    async def _fetch_cover(self, gallery: Gallery, folder: Path, cover_path: Path) -> None:
        if cover_path.exists():
            return
        data = await self.app.client.fetch_image(self.app.client.cover_url(gallery))
        folder.mkdir(parents=True, exist_ok=True)
        cover_path.write_bytes(data)
        await asyncio.to_thread(manifests.write_metadata, folder, gallery)

    async def _flush_progress(
        self,
        qid: int | None,
        folder: Path,
        manifest: dict,
        pages_done: int,
        total: int,
        bytes_done: int,
    ) -> None:
        try:
            await asyncio.to_thread(manifests.write_manifest, folder, manifest)
        except OSError as e:  # pragma: no cover - defensive
            log.debug("manifest write failed: %s", e)
        if qid:
            await self.db.queue_update(
                qid,
                {
                    "pages_done": pages_done,
                    "pages_total": total,
                    "progress": pages_done / max(1, total) * 100.0,
                    "bytes_done": bytes_done,
                },
            )

    # ------------------------------------------------------------------ folders

    def gallery_folder(self, gallery: Gallery) -> Path:
        """Template-rendered library folder: library/<artist>/<id> - <title>/."""
        rel = render_template(self.settings.folder_template, gallery.folder_vars())
        return self.app.library_root / rel

    def existing_folder_for(self, gallery_id: int) -> Path | None:
        """Dedup by gallery id: find any existing folder for this gallery."""
        import re as _re

        root = self.app.library_root
        if not root.exists():
            return None
        prefix = f"{gallery_id} -"
        prefix2 = f"{gallery_id}"
        for child in root.rglob(f"{gallery_id}*"):
            if child.is_dir():
                name = child.name
                if name.startswith(prefix) or _re.fullmatch(rf"{prefix2}", name):
                    return child
        return None


class _Canceled(Exception):
    pass


def gallery_from_dict(d: dict[str, Any]) -> Gallery:
    """Rebuild a Gallery (with pages) from a catalogue row."""
    import json

    raw = d.get("raw_json") or "{}"
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        data = {}
    if data:
        g = Gallery.from_api(data)
        g.tags = [
            Tag(id=t["id"], name=t["name"], type=t["type"], count=t.get("count", 0), url=t.get("url"))
            for t in (d.get("tags") or [])
        ]
        if not g.pages and d.get("num_pages"):
            g.pages = [Page(number=i + 1, ext="jpg") for i in range(int(d["num_pages"]))]
        return g
    g = Gallery(
        id=int(d["id"]),
        media_id=str(d.get("media_id") or ""),
        title_en=str(d.get("title_en") or ""),
        title_ja="",
        title_pretty=str(d.get("title_pretty") or ""),
        num_pages=int(d.get("num_pages") or 0),
    )
    g.tags = [
        Tag(id=t["id"], name=t["name"], type=t["type"], count=t.get("count", 0))
        for t in (d.get("tags") or [])
    ]
    if not g.pages and g.num_pages:
        g.pages = [Page(number=i + 1, ext="jpg") for i in range(g.num_pages)]
    return g


# ---------------------------------------------------------------------- import


async def import_folder(
    db,
    folder: Path,
    *,
    client=None,
    fetch_missing_detail: bool = False,
) -> int | None:
    """Register an existing library folder into the catalogue (plan §4).

    Adoption-first: reads ``metadata.json`` (Houdoku) or a numeric folder name
    ``^\\d+``; links the existing folder; never triggers a download. When
    metadata is missing and a client is provided, optionally fetches detail.
    Returns the gallery id, or None if the folder wasn't recognizable.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return None

    meta = manifests.read_metadata(folder)
    if meta:
        gallery = Gallery.from_api(meta)
    else:
        m = manifests.read_manifest(folder)
        if m:
            meta_from_manifest = {
                "id": m.get("gallery_id"),
                "media_id": m.get("media_id", ""),
                "title": {"english": m.get("title", ""), "japanese": "", "pretty": m.get("title", "")},
                "images": {"pages": [], "cover": {}, "thumbnail": {}},
                "tags": [],
                "num_pages": m.get("num_pages", 0),
                "upload_date": 0,
            }
            gallery = Gallery.from_api(meta_from_manifest)
        elif folder.name[:1].isdigit():
            import re

            match = re.match(r"^(\d+)", folder.name)
            if not match:
                return None
            gid = int(match.group(1))
            if client is not None and fetch_missing_detail:
                try:
                    gallery = await client.gallery(gid)
                except Exception:
                    gallery = _synthetic_gallery(gid, folder)
            else:
                gallery = _synthetic_gallery(gid, folder)
        else:
            return None

    # count real page files if the metadata under/over-reports
    page_files = sorted(
        p.name
        for p in folder.iterdir()
        if p.is_file()
        and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
        and p.stem.isdigit()
    )
    if page_files and not gallery.pages:
        gallery.pages = [
            Page(number=i + 1, ext=p.rsplit(".", 1)[-1].lower()) for i, p in enumerate(page_files)
        ]
        gallery.num_pages = len(gallery.pages)
    elif page_files and len(page_files) > len(gallery.pages):
        gallery.pages = [
            Page(number=i + 1, ext=p.rsplit(".", 1)[-1].lower()) for i, p in enumerate(page_files)
        ]
        gallery.num_pages = len(gallery.pages)

    await db.upsert_gallery(gallery)
    cover = next(
        (
            folder / f"cover{ext}"
            for ext in (".jpg", ".png", ".gif", ".webp")
            if (folder / f"cover{ext}").exists()
        ),
        None,
    )
    await db.update_user_data(
        gallery.id,
        {
            "download_status": "done",
            "folder_path": str(folder),
            "cover_path": str(cover) if cover else "",
        },
    )
    return gallery.id


def _synthetic_gallery(gid: int, folder: Path) -> Gallery:
    import re

    title = re.sub(r"^\d+\s*[-–]\s*", "", folder.name).strip() or f"Gallery {gid}"
    g = Gallery(
        id=gid,
        media_id="",
        title_en=title,
        title_ja="",
        title_pretty=title,
        num_pages=0,
    )
    g.pages = []
    return g


async def import_library(db, root: Path, *, client=None, fetch_missing_detail: bool = False) -> list[int]:
    """Scan a folder tree for gallery folders; register each into the catalogue."""
    root = Path(root)
    found: list[int] = []
    if not root.is_dir():
        return found
    # folders with metadata.json / .nhc.json, or direct numeric children
    candidates: list[Path] = []
    meta_names = {manifests.METADATA_NAME, manifests.MANIFEST_NAME}
    for child in sorted(root.iterdir()):
        if child.is_dir():
            has_meta = any((child / n).exists() for n in meta_names)
            numeric = bool(re.match(r"^\d+", child.name)) if (child.name[:1].isdigit()) else False
            if has_meta or numeric:
                candidates.append(child)
                continue
            # one level deep: <artist>/<id> - <title>/
            for sub in sorted(child.iterdir()):
                if sub.is_dir() and (
                    any((sub / n).exists() for n in meta_names) or sub.name[:1].isdigit()
                ):
                    candidates.append(sub)
    for c in candidates:
        gid = await import_folder(db, c, client=client, fetch_missing_detail=fetch_missing_detail)
        if gid:
            found.append(gid)
    return found


import re  # noqa: E402  (used above in helpers)
