"""FastAPI server: local REST API + SPA hosting (plan §5).

Binds 127.0.0.1 on a free port; a random token header check guards every
route (LAN exposure off by default). Media routes also accept ?token= so
plain <img> tags can load them. Only the client layer touches the network;
page/cover routes serve from disk first, else proxy through the client so
the strategy chain applies uniformly.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..client import Gallery, sniff_image_ext
from ..downloader import gallery_from_dict
from ..downloader.manifests import read_manifest

if TYPE_CHECKING:  # pragma: no cover
    from ..app import App

log = logging.getLogger("nhcatalogue.server")

UI_DIST = Path(__file__).resolve().parent.parent / "ui" / "dist"

# ------------------------------------------------------------------ live cache


class LiveCache:
    """Small LRU for live search passthrough (plan §3: "response cache")."""

    def __init__(self, maxsize: int = 64, ttl: float = 300.0):
        self.maxsize = maxsize
        self.ttl = ttl
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        hit = self._data.get(key)
        if not hit:
            return None
        ts, value = hit
        if time.monotonic() - ts > self.ttl:
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    def put(self, key: str, value: Any) -> None:
        self._data[key] = (time.monotonic(), value)
        self._data.move_to_end(key)
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)

    def pop(self, key: str) -> None:
        self._data.pop(key, None)


# --------------------------------------------------------------------- models


class GalleryAddBody(BaseModel):
    id: int
    # Big Pickle Phase 2: bare adds are ephemeral by default (no permanent
    # metadata/tags write). Only collection-adds / downloads persist.
    persist: bool = False


class CollectionBody(BaseModel):
    name: str
    description: str = ""


class CollectionMemberBody(BaseModel):
    gallery_id: int


class WatchlistBody(BaseModel):
    name: str
    query: str
    auto_download: bool = False
    enabled: bool = True


class WatchlistPatchBody(BaseModel):
    name: str | None = None
    query: str | None = None
    auto_download: bool | None = None
    enabled: bool | None = None


class ProgressBody(BaseModel):
    page: int = Field(ge=0)
    mode: str | None = None


class SettingsPatchBody(BaseModel):
    patch: dict[str, Any]


class DownloadActionBody(BaseModel):
    action: str  # pause | resume | cancel | retry


class CataloguePatchBody(BaseModel):
    favorite: bool | None = None
    rating: int | None = Field(default=None, ge=0, le=5)
    notes: str | None = None
    collection_id: int | None = None


# ----------------------------------------------------------------------- app


def create_app(app: App) -> FastAPI:
    cache = LiveCache()
    detail_cache = LiveCache(maxsize=256, ttl=600)  # live Gallery objects
    reader_cache = LiveCache(maxsize=256, ttl=600)  # parsed pages/folder state
    search_cache = LiveCache(maxsize=1024, ttl=600)  # lightweight search results
    tag_search_cache = LiveCache(maxsize=512, ttl=900)  # live tag autocomplete
    # Big Pickle Phase 2: galleries opened/previewed in this server session but
    # not written to the library. The UI marks them as "in catalogue" so the
    # reader works, while the permanent library stays clean.
    ephemeral_ids: set[int] = set()
    downloader = app.downloader
    syncer = app.syncer

    @asynccontextmanager
    async def lifespan(api: FastAPI):
        downloader.start()
        syncer.start()
        yield
        await downloader.stop()
        syncer.stop()

    api = FastAPI(title="nhcatalogue", lifespan=lifespan, docs_url=None, redoc_url=None)

    def guard(x_nhc_token: Annotated[str | None, Header()] = None) -> None:
        if not x_nhc_token or not secrets.compare_digest(x_nhc_token, app.token):
            raise HTTPException(401, "invalid or missing X-NHC-Token header")

    GUARD = Depends(guard)

    def media_guard(
        x_nhc_token: Annotated[str | None, Header()] = None,
        token: Annotated[str | None, Query()] = None,
    ) -> None:
        """Media routes also accept ?token= because <img> cannot send headers."""
        supplied = x_nhc_token or token
        if not supplied or not secrets.compare_digest(supplied, app.token):
            raise HTTPException(401, "invalid or missing token")

    MEDIA_GUARD = Depends(media_guard)

    def _cover_url(g: Gallery) -> str:
        """Cover endpoint URL, carrying the v2 CDN path when we have one."""
        if g.cover_path:
            return f"/api/cover/{g.id}?path={quote(g.cover_path, safe='')}"
        return f"/api/cover/{g.id}?media_id={g.media_id}&ext={g.cover_ext}"

    def _serialize_gallery(g: dict, *, include_raw: bool = False) -> dict:
        out = {
            "id": g["id"],
            "media_id": g["media_id"],
            "title": g.get("title_pretty") or g.get("title_en") or g.get("title_ja"),
            "title_en": g.get("title_en", ""),
            "title_ja": g.get("title_ja", ""),
            "title_pretty": g.get("title_pretty", ""),
            "num_pages": g.get("num_pages", 0),
            "upload_date": g.get("upload_date", 0),
            "language": g.get("language", ""),
            "category": g.get("category", ""),
            "tags": g.get("tags", []),
            "favorite": bool(g.get("favorite")),
            "rating": g.get("rating", 0),
            "notes": g.get("notes", ""),
            "read_progress_pages": g.get("read_progress_pages", 0),
            "last_read_at": g.get("last_read_at", ""),
            "download_status": g.get("download_status", "none"),
            "folder_path": g.get("folder_path", ""),
            "cover_url": f"/api/cover/{g['id']}",
            "in_catalogue": True,
            "persisted": True,
        }
        if include_raw:
            out["pages"] = [
                {"n": p.number, "ext": p.ext, "w": p.width, "h": p.height}
                for p in gallery_from_dict(g).pages
            ]
        return out

    def _live_gallery_payload(g: Gallery) -> dict:
        return {
            "id": g.id,
            "media_id": g.media_id,
            "title": g.title,
            "title_en": g.title_en,
            "title_ja": g.title_ja,
            "title_pretty": g.title_pretty,
            "num_pages": g.num_pages,
            "upload_date": g.upload_date,
            "language": g.language,
            "category": g.category,
            "tags": [
                {"id": t.id, "name": t.name, "type": t.type, "count": t.count}
                for t in g.tags
            ],
            "favorite": False,
            "rating": 0,
            "notes": "",
            "read_progress_pages": 0,
            "last_read_at": "",
            "download_status": "none",
            "folder_path": "",
            "cover_url": _cover_url(g),
            "in_catalogue": False,
            "persisted": False,
            "pages": [
                {"n": p.number, "ext": p.ext, "w": p.width, "h": p.height}
                for p in g.pages
            ],
        }

    async def _cached_live_gallery(gallery_id: int) -> Gallery:
        """Reuse a detail payload for the session instead of re-fetching it."""
        key = f"gallery:{int(gallery_id)}"
        g = detail_cache.get(key)
        if g is None:
            g = await app.client.gallery(gallery_id)
            detail_cache.put(key, g)
        return g

    async def _cache_cover(g: Gallery) -> None:
        """Best-effort cover cache; metadata persistence must not fail on it."""
        try:
            cover = await app.client.fetch_image(app.client.cover_url(g))
            ext = sniff_image_ext(cover)
            path = app.covers_dir / f"{g.id}.{ext}"
            path.write_bytes(cover)
            await app.db.update_user_data(g.id, {"cover_path": str(path)})
        except Exception as e:
            log.debug("cover cache failed for %s: %s", g.id, e)

    async def _promote_gallery(gallery_id: int) -> bool:
        """Make a previewed gallery permanent (Big Pickle Phase 2).

        Persists metadata/tags (fetching them live only when the gallery has no
        row yet), caches the cover, and moves retained temporary reader pages
        into the permanent page cache. Returns True when promotion happened.

        Collection-adds and downloads call this; it is idempotent and safe for
        galleries that are already permanent.
        """
        row = await app.db.get_gallery(gallery_id)
        was_in_collection = await app.db.gallery_in_any_collection(gallery_id)
        was_downloaded = bool(row and row.get("download_status") == "done")
        if was_in_collection or was_downloaded:
            ephemeral_ids.discard(int(gallery_id))
            return False
        if row is None:
            g = await _cached_live_gallery(gallery_id)
            await app.db.upsert_gallery(g)
        else:
            g = gallery_from_dict(row)
        cover_path = (row or {}).get("cover_path") or ""
        if not cover_path or not Path(cover_path).is_file():
            await _cache_cover(g)
        ephemeral_ids.discard(int(gallery_id))
        try:
            app.page_cache.promote_from(app.temp_page_cache, gallery_id)
        except Exception as e:  # page cache is best-effort; metadata already saved
            log.debug("preview page promotion failed for %s: %s", gallery_id, e)
        return True

    def _reader_state(row: dict) -> dict:
        """Parsed gallery + folder/manifest state, cached per page request."""
        folder_path = row.get("folder_path") or ""
        key = f"reader:{row['id']}:{folder_path}"
        state = reader_cache.get(key)
        if state is not None:
            return state
        g = gallery_from_dict(row)
        folder = Path(folder_path) if folder_path else None
        manifest = read_manifest(folder) if folder else None
        state = {
            "gallery": g,
            "folder": folder,
            "complete": bool(manifest and manifest.get("complete")),
        }
        reader_cache.put(key, state)
        return state

    # ---------------------------------------------------------------- search

    @api.get("/api/search", dependencies=[GUARD])
    async def api_search(
        q: str = "",
        page: int = 1,
        sort: str = "",
    ) -> dict:
        """Live passthrough search (cached) — Browse view data source."""
        key = f"search:{q}:{page}:{sort}"
        catalogue_ids = set(await app.db.gallery_ids())
        cached = cache.get(key)
        if cached is not None:
            # catalogue membership changes during the cache lifetime, so
            # recompute the cheap markers on every response.
            for item in cached["results"]:
                persisted = item["id"] in catalogue_ids
                item["persisted"] = persisted
                item["in_catalogue"] = persisted or item["id"] in ephemeral_ids
            return cached
        try:
            result = await app.client.search(q, page=page, sort=sort)
        except Exception as e:
            raise HTTPException(502, str(e)) from e
        results = []
        for g in result.galleries:
            persisted = g.id in catalogue_ids
            item = {
                "id": g.id,
                "media_id": g.media_id,
                "title": g.title,
                "title_en": g.title_en,
                "title_ja": g.title_ja,
                "title_pretty": g.title_pretty,
                "num_pages": g.num_pages,
                "upload_date": g.upload_date,
                "language": g.language,
                "tags": [
                    {"id": t.id, "name": t.name, "type": t.type, "count": t.count}
                    for t in g.tags
                ],
                "cover_url": _cover_url(g),
                "in_catalogue": persisted or g.id in ephemeral_ids,
                "persisted": persisted,
                # lightweight: the full page list is fetched only on add
                "pages": [],
                "favorite": False,
                "rating": 0,
                "notes": "",
                "read_progress_pages": 0,
                "last_read_at": "",
                "download_status": "none",
                "folder_path": "",
            }
            results.append(item)
            # opening a result should not trigger a second live detail request
            search_cache.put(f"gallery:{g.id}", item)
        payload = {
            "page": result.page,
            "total_pages": result.total_pages,
            "per_page": result.per_page,
            "results": results,
        }
        cache.put(key, payload)
        return payload

    def _mark_preview(gallery_id: int, out: dict) -> dict:
        """Apply session-preview markers to a live gallery payload."""
        out["persisted"] = False
        out["in_catalogue"] = int(gallery_id) in ephemeral_ids
        return out

    @api.get("/api/galleries/{gallery_id}", dependencies=[GUARD])
    async def api_gallery(gallery_id: int) -> dict:
        """Detail: catalogue row → session cache → search payload → live API."""
        row = await app.db.get_gallery(gallery_id)
        if row:
            return _serialize_gallery(row, include_raw=True)
        # a full detail payload already fetched this session wins
        cached = detail_cache.get(f"gallery:{int(gallery_id)}")
        if cached is not None:
            return _mark_preview(gallery_id, _live_gallery_payload(cached))
        # otherwise reuse the lightweight search result (no extra request)
        partial = search_cache.get(f"gallery:{int(gallery_id)}")
        if partial is not None:
            return {**_mark_preview(gallery_id, {**partial}), "partial": True}
        try:
            g = await _cached_live_gallery(gallery_id)
        except Exception as e:
            raise HTTPException(502, str(e)) from e
        return _mark_preview(gallery_id, _live_gallery_payload(g))

    # -------------------------------------------------------------- catalogue

    @api.get("/api/catalogue", dependencies=[GUARD])
    async def api_catalogue(
        q: str = "",
        sort: str = "added",
        limit: int = Query(60, ge=1, le=500),
        offset: int = Query(0, ge=0),
        favorite: bool | None = None,
        rating_min: int | None = Query(None, ge=0, le=5),
        downloaded: bool | None = None,
        collection: int | None = None,
        tag_id: int | None = None,
        include: list[str] = Query([]),
        exclude: list[str] = Query([]),
    ) -> dict:
        """Local library query: FTS, facets, filters (plan M3)."""
        inc = _parse_tag_filters(include)
        exc = _parse_tag_filters(exclude)
        res = await app.db.query_catalogue(
            q=q,
            include_tags=inc,
            exclude_tags=exc,
            favorite=favorite,
            rating_min=rating_min,
            downloaded=downloaded,
            collection=collection,
            tag_id=tag_id,
            sort=sort,
            limit=limit,
            offset=offset,
        )
        return {
            "items": [_serialize_gallery(g) for g in res["items"]],
            "total": res["total"],
            "limit": res["limit"],
            "offset": res["offset"],
        }

    def _parse_tag_filters(items: list[str]) -> list[dict[str, str]]:
        """Accept 'artist:mi' or 'type:name' or bare 'name' strings."""
        out = []
        for item in items:
            item = item.strip()
            if not item:
                continue
            if ":" in item:
                ty, _, name = item.partition(":")
                out.append({"type": ty.strip(), "name": name.strip()})
            else:
                out.append({"type": "general", "name": item})
        return out

    @api.post("/api/catalogue", dependencies=[GUARD])
    async def api_catalogue_add(body: GalleryAddBody) -> dict:
        """Add gallery {id} to catalogue: persist row + tags, cache cover.

        Big Pickle Phase 2: bare adds are ephemeral by default — no permanent
        metadata/tags write. ``persist: true`` opts into permanent library
        persistence (downloads and collection-adds use this).
        """
        existing = await app.db.get_gallery(body.id)
        if existing:
            ephemeral_ids.discard(body.id)
            return _serialize_gallery(existing, include_raw=True)
        if not body.persist:
            g = await _cached_live_gallery(body.id)
            out = _live_gallery_payload(g)
            out["in_catalogue"] = True
            out["persisted"] = False
            ephemeral_ids.add(int(body.id))
            return out
        try:
            await _promote_gallery(body.id)
        except Exception as e:
            raise HTTPException(502, f"{e.__class__.__name__}: {e}") from e
        row = await app.db.get_gallery(body.id)
        return _serialize_gallery(row, include_raw=True)

    @api.get("/api/catalogue/{gallery_id}", dependencies=[GUARD])
    async def api_catalogue_get(gallery_id: int) -> dict:
        row = await app.db.get_gallery(gallery_id)
        if not row:
            raise HTTPException(404, "not in catalogue")
        return _serialize_gallery(row, include_raw=True)

    @api.delete("/api/catalogue/{gallery_id}", dependencies=[GUARD])
    async def api_catalogue_delete(gallery_id: int, delete_files: bool = False) -> dict:
        row = await app.db.get_gallery(gallery_id)
        if not row:
            raise HTTPException(404, "not in catalogue")
        await app.db.delete_gallery(gallery_id, delete_files=delete_files)
        app.page_cache.purge(gallery_id)
        ephemeral_ids.discard(int(gallery_id))
        return {"deleted": gallery_id, "files_deleted": delete_files}

    @api.patch("/api/catalogue/{gallery_id}", dependencies=[GUARD])
    async def api_catalogue_patch(gallery_id: int, body: CataloguePatchBody) -> dict:
        row = await app.db.get_gallery(gallery_id)
        if not row:
            raise HTTPException(404, "not in catalogue")
        patch: dict[str, Any] = {}
        if body.favorite is not None:
            patch["favorite"] = body.favorite
        if body.rating is not None:
            patch["rating"] = body.rating
        if body.notes is not None:
            patch["notes"] = body.notes
        if patch:
            row = await app.db.update_user_data(gallery_id, patch)
        if body.collection_id is not None:
            try:
                await _promote_gallery(gallery_id)
            except Exception as e:
                raise HTTPException(502, f"{e.__class__.__name__}: {e}") from e
            await app.db.collection_add(body.collection_id, gallery_id)
            row = await app.db.get_gallery(gallery_id)
        return _serialize_gallery(row)

    @api.patch("/api/catalogue/{gallery_id}/progress", dependencies=[GUARD])
    async def api_progress(gallery_id: int, body: ProgressBody) -> dict:
        row = await app.db.set_progress(gallery_id, body.page)
        if not row:
            raise HTTPException(404, "not in catalogue")
        return {"id": gallery_id, "read_progress_pages": row["read_progress_pages"]}

    @api.get("/api/catalogue/bulk", dependencies=[GUARD])
    async def api_catalogue_bulk(ids: str = "") -> dict:
        wanted = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        items = []
        for gid in wanted:
            row = await app.db.get_gallery(gid)
            if row:
                items.append(_serialize_gallery(row))
        return {"items": items}

    # -------------------------------------------------------------------- tags

    @api.get("/api/tags", dependencies=[GUARD])
    async def api_tags(
        q: str = "",
        type: str = "",
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ) -> dict:
        """Tag search: catalogue tags by name fragment and/or type with gallery
        counts, ordered by count — powers Tags browser + autocomplete."""
        res = await app.db.tag_search(q=q, type_=type, limit=limit, offset=offset)
        return res

    @api.get("/api/tags/search", dependencies=[GUARD])
    async def api_tags_search(
        q: str = "",
        type: str = "",
        limit: int = Query(50, ge=1, le=50),
    ) -> dict:
        """Live nhentai tag search for the Browse picker.

        An empty ``q`` returns the site's most popular tags, so you can
        discover tags before saving anything locally. Results are cached; if
        the network is unavailable we fall back to tags already in the local
        catalogue so the picker keeps working offline.
        """
        key = f"tagsearch:{type}:{q.strip().lower()}:{limit}"
        cached = tag_search_cache.get(key)
        if cached is not None:
            return cached
        try:
            tags = await app.client.search_tags(q, type_=type, limit=limit)
        except Exception as e:
            log.debug("live tag search failed (%s); using local tags", e)
            res = await app.db.tag_search(q=q, type_=type, limit=limit)
            items = [
                {
                    "id": t["id"],
                    "name": t["name"],
                    "type": t["type"],
                    "count": t.get("api_count", 0) or 0,
                    "gallery_count": t.get("gallery_count", 0) or 0,
                }
                for t in res["items"]
            ]
            return {"items": items, "source": "local", "error": str(e)}
        payload = {
            "items": [
                {
                    "id": t.id,
                    "name": t.name,
                    "type": t.type,
                    "count": t.count,
                    "gallery_count": 0,
                }
                for t in tags
            ],
            "source": "live",
        }
        tag_search_cache.put(key, payload)
        return payload

    @api.get("/api/tags/facets", dependencies=[GUARD])
    async def api_tag_facets(
        include: list[str] = Query([]), exclude: list[str] = Query([])
    ) -> dict:
        inc = _parse_tag_filters(include)
        exc = _parse_tag_filters(exclude)
        return await app.db.tag_facets(include_tags=inc, exclude_tags=exc)

    @api.get("/api/tags/groups", dependencies=[GUARD])
    async def api_tags_groups() -> dict:
        """Tags browser: everything grouped by type with counts."""
        return await app.db.tags_by_group()

    @api.get("/api/tags/{tag_id}/galleries", dependencies=[GUARD])
    async def api_tag_galleries(
        tag_id: int,
        limit: int = Query(60, ge=1, le=500),
        offset: int = Query(0, ge=0),
        sort: str = "added",
    ) -> dict:
        res = await app.db.query_catalogue(tag_id=tag_id, sort=sort, limit=limit, offset=offset)
        return {
            "items": [_serialize_gallery(g) for g in res["items"]],
            "total": res["total"],
            "limit": res["limit"],
            "offset": res["offset"],
        }

    @api.get("/api/tags/suggest", dependencies=[GUARD])
    async def api_tags_suggest(q: str = "", limit: int = 10) -> dict:
        """Autocomplete for the search bar: catalogue tags first (offline,
        normalized matching), optionally blended with live API suggestions."""
        res = await app.db.tag_search(q=q, limit=limit)
        items = [
            {"id": t["id"], "name": t["name"], "type": t["type"], "count": t["gallery_count"]}
            for t in res["items"]
            if t["gallery_count"] > 0 or t["api_count"] > 0
        ]
        return {"items": items}

    # -------------------------------------------------------------- collections

    @api.get("/api/collections", dependencies=[GUARD])
    async def api_collections() -> dict:
        return {"items": await app.db.collections()}

    @api.post("/api/collections", dependencies=[GUARD])
    async def api_collection_create(body: CollectionBody) -> dict:
        return await app.db.collection_create(body.name, body.description)

    @api.delete("/api/collections/{collection_id}", dependencies=[GUARD])
    async def api_collection_delete(collection_id: int) -> dict:
        # remember members: after the delete, purge caches of galleries that no
        # longer belong to any collection.
        members = await app.db.collection_gallery_ids(collection_id)
        await app.db.collection_delete(collection_id)
        for gid in members:
            if await app.db.gallery_collection_count(gid) == 0:
                app.page_cache.purge(gid)
        return {"deleted": collection_id}

    @api.post("/api/collections/{collection_id}/galleries", dependencies=[GUARD])
    async def api_collection_add(collection_id: int, body: CollectionMemberBody) -> dict:
        gallery_id = body.gallery_id
        # Promotion (Big Pickle Phase 2): the comic becomes permanent storage —
        # persist metadata/tags, cache the cover and promote retained temporary
        # reader pages. Fail before touching the shelf if metadata is unavailable.
        try:
            await _promote_gallery(gallery_id)
        except Exception as e:
            raise HTTPException(
                502, f"could not add #{gallery_id} to catalogue: {e.__class__.__name__}: {e}"
            ) from e
        await app.db.collection_add(collection_id, gallery_id)
        return {"ok": True}

    @api.delete("/api/collections/{collection_id}/galleries/{gallery_id}", dependencies=[GUARD])
    async def api_collection_remove(collection_id: int, gallery_id: int) -> dict:
        await app.db.collection_remove(collection_id, gallery_id)
        # collection-only cache: last shelf removal purges the gallery's pages
        if await app.db.gallery_collection_count(gallery_id) == 0:
            app.page_cache.purge(gallery_id)
        return {"ok": True}

    # --------------------------------------------------------------- watchlists

    @api.get("/api/watchlists", dependencies=[GUARD])
    async def api_watchlists() -> dict:
        return {"items": await app.db.watchlists()}

    @api.post("/api/watchlists", dependencies=[GUARD])
    async def api_watchlist_create(body: WatchlistBody) -> dict:
        return await app.db.watchlist_create(body.name, body.query, body.auto_download)

    @api.patch("/api/watchlists/{watchlist_id}", dependencies=[GUARD])
    async def api_watchlist_patch(watchlist_id: int, body: WatchlistPatchBody) -> dict:
        patch = {k: v for k, v in body.model_dump().items() if v is not None}
        wl = await app.db.watchlist_update(watchlist_id, patch)
        if not wl:
            raise HTTPException(404, "watchlist not found")
        return wl

    @api.delete("/api/watchlists/{watchlist_id}", dependencies=[GUARD])
    async def api_watchlist_delete(watchlist_id: int) -> dict:
        await app.db.watchlist_delete(watchlist_id)
        return {"deleted": watchlist_id}

    @api.post("/api/watchlists/{watchlist_id}/sync-now", dependencies=[GUARD])
    async def api_watchlist_sync_now(watchlist_id: int) -> dict:
        wl = next(
            (w for w in await app.db.watchlists() if w["id"] == watchlist_id), None
        )
        if not wl:
            raise HTTPException(404, "watchlist not found")
        result = await syncer.sync_watchlist(wl, force=True)
        return result

    @api.post("/api/watchlists/{watchlist_id}/seen", dependencies=[GUARD])
    async def api_watchlist_seen(watchlist_id: int) -> dict:
        """Clear the unread badge."""
        wl = await app.db.watchlist_update(watchlist_id, {"unread_count": 0})
        if not wl:
            raise HTTPException(404, "watchlist not found")
        return wl

    # ---------------------------------------------------------------- downloads

    @api.get("/api/downloads", dependencies=[GUARD])
    async def api_downloads() -> dict:
        return {"items": await app.db.queue_list()}

    @api.get("/api/downloads/active", dependencies=[GUARD])
    async def api_downloads_active() -> dict:
        items = await app.db.queue_list()
        return {"items": [q for q in items if q["status"] in ("queued", "running", "paused")]}

    @api.post("/api/downloads/{gallery_id}", dependencies=[GUARD])
    async def api_download_action(gallery_id: int, body: DownloadActionBody) -> dict:
        actions = {
            "pause": downloader.pause,
            "resume": downloader.resume,
            "cancel": lambda: asyncio.ensure_future(downloader.cancel(gallery_id)),
            "retry": lambda: asyncio.ensure_future(downloader.retry(gallery_id)),
        }
        fn = actions.get(body.action)
        if fn is None:
            raise HTTPException(400, f"unknown action {body.action!r}")
        maybe_coro = fn()
        if asyncio.iscoroutine(maybe_coro):
            await maybe_coro
        q = await app.db.queue_by_gallery(gallery_id)
        return q or {"ok": True}

    @api.post("/api/downloads", dependencies=[GUARD])
    async def api_download_enqueue(body: GalleryAddBody) -> dict:
        """Queue a download (persists metadata/tags and promotes temp pages)."""
        try:
            await _promote_gallery(body.id)
        except Exception as e:
            raise HTTPException(502, f"{e.__class__.__name__}: {e}") from e
        qid = await downloader.enqueue(body.id)
        if qid is None:
            raise HTTPException(409, "gallery not queued (missing or already active)")
        return await app.db.queue_by_gallery(body.id)

    # ------------------------------------------------------------------- reader

    @api.get("/api/reader/{gallery_id}/page/{page_number}", dependencies=[MEDIA_GUARD])
    async def api_reader_page(gallery_id: int, page_number: int) -> Response:
        """Library folder → permanent cache → temporary reader cache → CDN.

        Big Pickle Phase 2: permanent cache holds collection/downloaded pages;
        the temporary reader cache (10s retention) holds pages for comics that
        were opened but not yet permanently saved.
        """
        if page_number < 1:
            raise HTTPException(400, "page numbers are 1-based")
        row = await app.db.get_gallery(gallery_id)
        if row is None:
            # Big Pickle Phase 2: comics without a permanent library entry (bare
            # adds) resolve live and are served through the temporary reader cache.
            try:
                g = await _cached_live_gallery(gallery_id)
            except Exception as e:
                raise HTTPException(502, f"{e.__class__.__name__}: {e}") from e
            pages = g.pages
            folder: Path | None = None
            complete = False
            cache_allowed = False
        else:
            state = _reader_state(row)
            g: Gallery = state["gallery"]
            pages = g.pages
            folder = state["folder"]
            complete = state["complete"]
            # permanent storage for collections / downloads, temporary reader
            # cache for comics opened but not yet saved
            cache_allowed = bool(await app.db.gallery_in_any_collection(gallery_id)) or (
                row.get("download_status") == "done"
            )

        # 1) downloaded library folder is authoritative — never duplicate it
        if folder:
            if pages and page_number <= len(pages):
                page = pages[page_number - 1]
                pad = max(3, len(str(len(pages))))
                candidate = folder / f"{page_number:0{pad}d}.{page.ext}"
                if candidate.exists():
                    return _file_response(candidate)
                # try other extensions (imports sometimes mismatch)
                for ext in ("jpg", "png", "gif", "webp", "bmp", "jpeg"):
                    alt = folder / f"{page_number:0{pad}d}.{ext}"
                    if alt.exists():
                        return _file_response(alt)
                if complete:
                    raise HTTPException(404, "page missing from downloaded folder")
            cache_allowed = False  # library folder is the local copy
        else:
            # 2) lazy cache: permanent for collections / downloaded galleries,
            #    temporary reader cache for comics opened but not yet saved
            #    (Big Pickle Phase 2 — temporary reader-cache lifecycle).
            if pages and page_number <= len(pages):
                cache_store = app.page_cache if cache_allowed else app.temp_page_cache
                cached = cache_store.get(
                    gallery_id, page_number, pages[page_number - 1].ext, len(pages)
                )
                if cached is not None:
                    return _file_response(cached)

        # 3) stream from the CDN through the strategy chain
        try:
            if not pages:
                g = await app.client.gallery(gallery_id)
                pages = g.pages
                reader_cache.put(
                    f"reader:{gallery_id}:{str(folder) if folder is not None else ''}",
                    {"gallery": g, "folder": folder, "complete": False},
                )
            if page_number > len(pages):
                raise HTTPException(404, f"page {page_number} beyond {len(pages)}")
            data = await app.client.fetch_image(
                app.client.page_url(g, page_number)
            )
        except HTTPException:
            raise
        except Exception as e:
            # Big Pickle Phase 3: keep the failure class visible in the message
            # so callers/UI can map retries and surface the real cause.
            raise HTTPException(502, f"{e.__class__.__name__}: {e}") from e

        ext = sniff_image_ext(data)
        if (
            app.settings.page_cache_max_mb > 0
            and pages
            and page_number <= len(pages)
            and not folder
        ):
            # temporary (non-permanent) reader entries land in the temp cache;
            # permanent ones (collections / downloads) land in the permanent cache
            cache_store = app.page_cache if cache_allowed else app.temp_page_cache
            await asyncio.to_thread(
                cache_store.put,
                gallery_id,
                page_number,
                pages[page_number - 1].ext,
                data,
                len(pages),
            )
        return Response(
            content=data,
            media_type=f"image/{ext}",
            headers={"Cache-Control": "private, max-age=86400"},
        )

    @api.get("/api/reader/{gallery_id}/info", dependencies=[GUARD])
    async def api_reader_info(gallery_id: int) -> dict:
        row = await app.db.get_gallery(gallery_id)
        if row is None:
            # Big Pickle Phase 2: uncatalogued (ephemeral) comics resolve live.
            try:
                g = await _cached_live_gallery(gallery_id)
            except Exception as e:
                raise HTTPException(502, f"{e.__class__.__name__}: {e}") from e
            pages = g.pages
            return {
                "id": gallery_id,
                "title": g.title_pretty or g.title_en or g.title_ja,
                "num_pages": len(pages),
                "pages": [{"n": p.number, "ext": p.ext} for p in pages],
                "read_progress_pages": 0,
                "downloaded": False,
                "folder_path": "",
            }
        state = _reader_state(row)
        g = state["gallery"]
        pages = g.pages
        if not pages:
            try:
                g = await _cached_live_gallery(gallery_id)
                pages = g.pages
                reader_cache.put(
                    f"reader:{gallery_id}:{row.get('folder_path') or ''}",
                    {"gallery": g, "folder": state["folder"], "complete": False},
                )
            except Exception:
                pages = []
        return {
            "id": gallery_id,
            "title": row.get("title_pretty") or row.get("title_en"),
            "num_pages": len(pages),
            "pages": [{"n": p.number, "ext": p.ext} for p in pages],
            "read_progress_pages": row.get("read_progress_pages", 0),
            "downloaded": row.get("download_status") == "done",
            "folder_path": row.get("folder_path", ""),
        }

    # -------------------------------------------------------------------- cover

    @api.get("/api/cover/{gallery_id}", dependencies=[MEDIA_GUARD])
    async def api_cover(
        gallery_id: int, media_id: str = "", ext: str = "jpg", path: str = ""
    ) -> Response:
        """Local cover cache; else fetch via the client and cache."""
        cached = list(app.covers_dir.glob(f"{gallery_id}.*"))
        if cached:
            return _file_response(cached[0])
        row = await app.db.get_gallery(gallery_id)
        if row is None:
            if path:
                data = await _proxy_path(path)
                ext_use = path.rsplit(".", 1)[-1].lower()
                media = f"image/{ext_use if ext_use != 'jpg' else 'jpeg'}"
                return Response(content=data, media_type=media)
            if not media_id:
                raise HTTPException(404, "no cover without media_id for uncatalogued gallery")
            ext_use = ext or "jpg"
            data = await _proxy_thumb(gallery_id, media_id, ext_use, "cover")
            return Response(
                content=data, media_type=f"image/{ext_use if ext_use != 'jpg' else 'jpeg'}"
            )
        g = gallery_from_dict(row)
        try:
            data = await app.client.fetch_image(app.client.cover_url(g))
        except Exception as e:
            raise HTTPException(502, str(e)) from e
        ext2 = sniff_image_ext(data)
        path = app.covers_dir / f"{gallery_id}.{ext2}"
        path.write_bytes(data)
        await app.db.update_user_data(gallery_id, {"cover_path": str(path)})
        return Response(
            content=data,
            media_type=f"image/{ext2}",
            headers={"Cache-Control": "private, max-age=86400"},
        )

    async def _proxy_path(path: str) -> bytes:
        from ..config import THUMB_HOSTS

        url = f"https://{THUMB_HOSTS[0]}/{path.lstrip('/')}"
        try:
            return await app.client.fetch_image(url)
        except Exception as e:
            raise HTTPException(502, str(e)) from e

    async def _proxy_thumb(gallery_id: int, media_id: str, ext: str, kind: str) -> bytes:
        # v2 covers are served as webp with the original extension in the name
        # (``cover.jpg.webp``); fall back to the legacy path for old mirrors.
        candidates = []
        if ext and ext != "webp":
            candidates.append(f"galleries/{media_id}/cover.{ext}.webp")
        candidates.extend(
            [f"galleries/{media_id}/cover.{ext or 'jpg'}", f"galleries/{media_id}/cover.webp"]
        )
        last: HTTPException | None = None
        for path in candidates:
            try:
                return await _proxy_path(path)
            except HTTPException as e:
                last = e
        raise last or HTTPException(502, "cover not found")

    # ------------------------------------------------------------------ settings

    @api.get("/api/settings", dependencies=[GUARD])
    async def api_settings_get() -> dict:
        s = app.settings.to_dict()
        s["library_root"] = app.resolved_library_root  # resolved for display
        s["token"] = app.token
        s["has_curl_cffi"] = __import__("nhcatalogue.client", fromlist=["HAS_CURL_CFFI"]).HAS_CURL_CFFI
        s["domain_stats"] = app.client.rotator.stats()
        s["blocked"] = app.client.last_blocked is not None
        if app.client.last_blocked:
            s["blocked_message"] = str(app.client.last_blocked)
            s["blocked_strategies"] = app.client.last_blocked.strategies[-8:]
        s["validate"] = app.settings.validate()
        return s

    @api.put("/api/settings", dependencies=[GUARD])
    async def api_settings_put(body: SettingsPatchBody) -> dict:
        app.settings.apply_patch(body.patch)
        errors = app.settings.validate()
        if errors:
            raise HTTPException(422, "; ".join(errors))
        app.save_settings()
        app.client.refresh_from_settings()
        if "download_workers" in body.patch:
            downloader.set_workers(app.settings.download_workers)
        if "page_cache_max_mb" in body.patch:
            cache_bytes = app.settings.page_cache_max_mb * 1024 * 1024
            app.page_cache.set_max_bytes(cache_bytes)
            app.temp_page_cache.set_max_bytes(cache_bytes)
        if "temp_page_cache_ttl_s" in body.patch:
            app.temp_page_cache.set_ttl(app.settings.temp_page_cache_ttl_s)
        return await api_settings_get()

    @api.post("/api/settings/test", dependencies=[GUARD])
    async def api_settings_test() -> dict:
        """Proxy/connection test button — reports live status."""
        return await app.client.test_connection()

    @api.post("/api/settings/test-proxy", dependencies=[GUARD])
    async def api_settings_test_proxy() -> dict:
        """Test just the proxy: connect through it to the first domain."""
        res = await app.client.test_connection()
        res["proxy"] = app.settings.proxy or "(none)"
        return res

    # ------------------------------------------------------------------ import

    @api.post("/api/import", dependencies=[GUARD])
    async def api_import(path: str = "") -> dict:
        from ..downloader import import_library

        if not path:
            path = str(app.library_root)
        ids = await import_library(app.db, Path(path), client=app.client)
        for gid in ids:
            ephemeral_ids.discard(int(gid))
        return {"imported": len(ids), "ids": ids}

    # ------------------------------------------------------------------- misc

    @api.get("/api/health", dependencies=[GUARD])
    async def api_health() -> dict:
        return {
            "ok": True,
            "version": __import__("nhcatalogue").__version__,
            "galleries": await app.db.count_galleries(),
            "transport": app.client.transport.name,
            "curl_cffi": __import__("nhcatalogue.client", fromlist=["HAS_CURL_CFFI"]).HAS_CURL_CFFI,
        }

    @api.get("/api/stats", dependencies=[GUARD])
    async def api_stats() -> dict:
        tags = await app.db.tag_search(limit=5)
        return {
            "galleries": await app.db.count_galleries(),
            "top_tags": tags["items"][:5],
            "domain_stats": app.client.rotator.stats(),
        }

    # ----------------------------------------------------------------- static UI

    if UI_DIST.exists():
        api.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")

        @api.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(UI_DIST / "index.html")

        @api.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str) -> Response:
            candidate = UI_DIST / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(UI_DIST / "index.html")
    else:  # dev fallback: explain how to get the UI
        @api.get("/", include_in_schema=False)
        async def index() -> Response:
            return JSONResponse(
                {
                    "app": "nhcatalogue",
                    "hint": "UI not built. Run: cd ui-src && npm install && npm run build "
                    "(output goes to ../src/nhcatalogue/ui/dist)",
                    "api": "/api/health",
                },
                status_code=200,
            )

    return api


def _file_response(path: Path) -> FileResponse:
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


def pick_free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def run_server(app: App, host: str = "127.0.0.1", port: int = 0, *, open_browser: bool = False):
    """Start uvicorn (optionally opening the default browser) and block."""
    import uvicorn

    if port == 0:
        port = pick_free_port()
    api = create_app(app)
    config = uvicorn.Config(api, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    url = f"http://{host}:{port}/"
    print(f"nhcatalogue serving on {url}  (token: {app.token})")
    print(f"open in a browser: {url}#token={app.token}")
    if open_browser:
        import webbrowser

        # the SPA bootstraps its token from the URL hash on first load
        webbrowser.open(f"{url}#token={app.token}")
    await server.serve()
