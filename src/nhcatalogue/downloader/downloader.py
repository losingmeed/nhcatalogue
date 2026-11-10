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
        self._running = False
        self._cancel_requested: set[int] = set()  # gallery ids to cancel
        self._retry_requested: set[int] = set()
        self._paused = asyncio.Event()
        self._paused.set()  # set = not paused
        self._wakeup = asyncio.Event()
        self._wakeup.set()

    # ------------------------------------------------------------------ control

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.workers = max(1, self.settings.download_workers)
        for i in range(self.workers):
            self._tasks.append(asyncio.create_task(self._worker(i), name=f"dl-worker-{i}"))

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        # wake anything blocked on an empty queue
        for _ in range(self.workers):
            self._queue.put_nowait(None)

    async def enqueue(self, gallery_id: int) -> int | None:
        """Add a gallery to the download queue (dedup: one active item each)."""
        gallery = await self.db.get_gallery(gallery_id)
        if gallery is None:
            log.warning("enqueue: gallery %s not in catalogue (add it first)", gallery_id)
            return None
        qid = await self.db.queue_add(gallery_id, gallery.get("num_pages", 0) or 0)
        await self.db.update_user_data(gallery_id, {"download_status": "queued"})
        self.start()
        self._queue.put_nowait(int(gallery_id))
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
        q = await self.db.queue_by_gallery(gallery_id)
        if q:
            await self.db.queue_update(q["id"], {"status": "queued", "retries": q["retries"] + 1, "error": ""})
            self._retry_requested.discard(int(gallery_id))
            await self.db.update_user_data(gallery_id, {"download_status": "queued"})
            self.start()
            self._queue.put_nowait(int(gallery_id))

    # ------------------------------------------------------------------- worker

    async def _worker(self, index: int) -> None:
        while self._running:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=2.0)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                return
            if item is None:
                return
            try:
                await self._paused.wait()
                await self._download_item(item)
            except asyncio.CancelledError:
                return
            except Exception as e:  # pragma: no cover - defensive
                log.exception("worker %s failed on gallery %s", index, item)
                q = await self.db.queue_by_gallery(item)
                if q:
                    await self.db.queue_update(
                        q["id"], {"status": "error", "error": f"{e.__class__.__name__}: {e}"}
                    )
                await self.db.update_user_data(item, {"download_status": "error"})
            finally:
                self._queue.task_done()

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
            await self.db.queue_update(qid, {"status": "running", "pages_total": total, "folder_path": str(folder)})

        manifest = manifests.read_manifest(folder)
        if manifest is None or manifest.get("gallery_id") != gallery.id:
            manifest = manifests.build_manifest(gallery)
        downloaded = manifest.get("downloaded", {})

        try:
            # cover (never blocks completion; redownloaded if missing)
            cover_path = folder / f"cover.{gallery.cover_ext}"
            if not cover_path.exists():
                cover_bytes = await self.app.client.fetch_image(
                    self.app.client.cover_url(gallery)
                )
                folder.mkdir(parents=True, exist_ok=True)
                cover_path.write_bytes(cover_bytes)
                manifests.write_metadata(folder, gallery)

            pages_done = 0
            for page in gallery.pages:
                if gallery_id in self._cancel_requested:
                    raise _Canceled()
                await self._paused.wait()  # pause support mid-download
                if manifests.verify_page(manifest, page.number, folder, page.ext):
                    pages_done += 1
                    continue  # resume: already on disk and intact
                fname = page_filename(page.number, page.ext, total)
                dest = folder / fname
                url = self.app.client.page_url(gallery, page.number)
                data = await self.app.client.fetch_image(url)
                folder.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                manifests.mark_page_downloaded(manifest, page.number, len(data))
                manifests.write_manifest(folder, manifest)
                pages_done += 1
                if qid:
                    await self.db.queue_update(
                        qid,
                        {
                            "pages_done": pages_done,
                            "pages_total": total,
                            "progress": pages_done / max(1, total) * 100.0,
                        },
                    )

            manifest["complete"] = True
            manifest["downloaded"] = downloaded
            manifests.write_manifest(folder, manifest)
            manifests.write_metadata(folder, gallery)
            if qid:
                await self.db.queue_update(
                    qid, {"status": "done", "progress": 100.0, "pages_done": total, "folder_path": str(folder)}
                )
            await self.db.update_user_data(
                gallery_id,
                {"download_status": "done", "folder_path": str(folder), "cover_path": str(cover_path)},
            )
        except _Canceled:
            manifest["complete"] = False
            manifests.write_manifest(folder, manifest)
            if qid:
                await self.db.queue_update(qid, {"status": "canceled"})
            await self.db.update_user_data(gallery_id, {"download_status": "partial"})
            self._cancel_requested.discard(gallery_id)
        except Exception as e:
            if qid:
                await self.db.queue_update(
                    qid, {"status": "error", "error": f"{e.__class__.__name__}: {e}"}
                )
            await self.db.update_user_data(gallery_id, {"download_status": "error"})
            raise

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
