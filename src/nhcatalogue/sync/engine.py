"""Watchlist sync engine (plan §3 / M4).

Background asyncio task: every watchlist query runs against the live search
API at a configurable interval (default 6h, min 1h), rate-limited by the
client. A per-watchlist ``last_seen`` cursor (highest gallery id already seen)
prevents re-processing. New results are auto-added to the catalogue and, if
``auto_download`` is set, queued for download. Unread badges count new items
since the last UI visit.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ..client.models import sniff_image_ext

if TYPE_CHECKING:  # pragma: no cover
    from ..app import App

log = logging.getLogger("nhcatalogue.sync")


class SyncEngine:
    def __init__(self, app: App):
        self.app = app
        self.db = app.db
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._wake = asyncio.Event()  # manual "sync now" trigger

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._wake.clear()
            self._task = asyncio.create_task(self._loop(), name="watchlist-sync")

    def stop(self) -> None:
        self._stop_event.set()
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
        self._task = None

    async def trigger(self) -> None:
        """Manual sync-now button: wake the loop early."""
        self._wake.set()

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval_seconds())
            except TimeoutError:
                pass  # interval elapsed
            except asyncio.CancelledError:
                return
            if self._stop_event.is_set():
                return
            self._wake.clear()
            try:
                await self.sync_all()
            except Exception:  # pragma: no cover - keep the loop alive
                log.exception("watchlist sync round failed")

    def _interval_seconds(self) -> float:
        # wake up frequently but only *run* a watchlist when its own interval
        # elapsed; keeps manual sync responsive.
        return min(60.0, max(1.0, 60.0))

    async def sync_all(self) -> dict:
        results = {}
        for wl in await self.db.watchlists():
            if not wl["enabled"]:
                continue
            try:
                results[wl["id"]] = await self.sync_watchlist(wl)
            except Exception as e:
                log.warning("watchlist %s (%s) failed: %s", wl["id"], wl["name"], e)
                results[wl["id"]] = {"error": str(e)}
        return results

    async def sync_watchlist(self, wl: dict, *, force: bool = False) -> dict:
        """Run one watchlist; returns counts of new galleries added/queued.

        ``force=True`` (the manual sync-now button) skips the interval check.
        """
        if force or self._due(wl):
            return await self._run_watchlist(wl)
        return {"skipped": "not due"}

    def _due(self, wl: dict) -> bool:
        hours = max(1.0, float(self.app.settings.watchlist_interval_hours or 6.0))
        if not wl["last_sync"]:
            return True
        try:
            last = datetime.fromisoformat(wl["last_sync"])
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
        except ValueError:
            return True
        elapsed = (datetime.now(UTC) - last).total_seconds() / 3600.0
        return elapsed >= hours

    async def _run_watchlist(self, wl: dict) -> dict:
        client = self.app.client
        cursor = int(wl.get("last_seen_id") or 0)
        added: list[int] = []
        new_cursor = cursor

        result = await client.search(wl["query"], page=1, sort="date")
        for g in result.galleries:
            if g.id <= cursor:
                continue
            # v2 search results are lightweight (no pages/tags) — fetch detail
            # so downloads know the real per-page extensions and the catalogue
            # has tags. A detail failure must not kill the sync.
            try:
                detail = await client.gallery(g.id)
            except Exception as e:
                log.warning("watchlist %s: detail fetch failed for %s: %s", wl["id"], g.id, e)
                continue
            await self.db.upsert_gallery(detail)
            # cover cache for instant offline catalogue grid
            try:
                cover = await client.fetch_image(client.cover_url(detail))
                await self._cache_cover(detail.id, cover)
            except Exception as e:  # cover failure must not kill the sync
                log.debug("cover fetch failed for %s: %s", detail.id, e)
            added.append(detail.id)
            new_cursor = max(new_cursor, detail.id)
            if wl["auto_download"]:
                await self.app.downloader.enqueue(detail.id)

        await self.db.watchlist_update(
            wl["id"],
            {
                "last_seen_id": new_cursor,
                "last_sync": datetime.now(UTC).isoformat(),
                "unread_count": (wl.get("unread_count") or 0) + len(added),
            },
        )
        return {"added": added, "count": len(added)}

    async def _cache_cover(self, gallery_id: int, data: bytes) -> None:
        ext = sniff_image_ext(data)
        cache = self.app.covers_dir / f"{gallery_id}.{ext}"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(data)
        await self.db.update_user_data(gallery_id, {"cover_path": str(cache)})
