"""Rule 34 API routes — kept separate so the nhentai paths stay untouched."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from ..client.rule34 import decode_id
from ..client.sources import SOURCES

log = logging.getLogger("nhcatalogue.rule34.routes")

LARGE_FILE_THRESHOLD = 10 * 1024 * 1024


def build_rule34_router(app, guard, media_guard) -> APIRouter:
    router = APIRouter()

    @router.get("/api/sources", dependencies=[guard])
    async def api_sources() -> dict:
        return {
            "sources": [
                {
                    "name": s.name,
                    "label": s.label,
                    "icon": s.icon,
                    "is_comic": s.is_comic,
                    "has_video": s.has_video,
                    "tag_types": list(s.tag_types),
                }
                for s in SOURCES.values()
            ]
        }

    @router.get("/api/rule34/status", dependencies=[guard])
    async def api_rule34_status() -> dict:
        client = app.source_client("rule34")
        return {
            "domain": client.domain,
            "api_host": client.api_host,
            "configured": client.configured,
            "needs_credentials": not client.configured,
            "message": (
                ""
                if client.configured
                else "rule34 needs an account id and API key — set them in Settings"
            ),
        }

    async def _resolve(gallery_id: int):
        from ..downloader import gallery_from_dict

        row = await app.db.get_gallery(gallery_id)
        if row is not None:
            return gallery_from_dict(row), row
        g = await app.source_client("rule34").gallery(gallery_id)
        return g, None

    async def _describe(g, row: dict | None) -> dict[str, Any]:
        pages = [
            {
                "n": p.number,
                "ext": p.ext,
                "w": p.width,
                "h": p.height,
                "kind": g.media_kind,
            }
            for p in g.pages
        ]
        size = int(getattr(g, "file_size", 0) or 0)
        if row:
            size = size or int(row.get("file_size") or 0)
        if not size and g.pages:
            # The dapi does not report file sizes; ask the CDN for them so
            # the large-download warning can still fire.
            client = app.source_client("rule34")
            size = await client.media_size(g.pages[0].path)
        return {
            "id": g.id,
            "post_id": decode_id(g.id),
            "media_id": g.media_id,
            "title": g.title_pretty or g.title_en,
            "source": "rule34",
            "kind": g.media_kind,
            "file_size": size,
            "large_file": size > LARGE_FILE_THRESHOLD,
            "large_file_threshold": LARGE_FILE_THRESHOLD,
            "num_pages": g.num_pages,
            "tags": [
                {"id": t.id, "name": t.name, "type": t.type, "count": t.count}
                for t in g.tags
            ],
            "pages": pages,
            "in_catalogue": row is not None,
        }

    @router.get("/api/rule34/post/{gallery_id}", dependencies=[guard])
    async def api_rule34_post(gallery_id: int) -> dict:
        try:
            g, row = await _resolve(gallery_id)
        except Exception as e:
            raise HTTPException(502, f"{e.__class__.__name__}: {e}") from e
        return await _describe(g, row)

    @router.get("/api/rule34/media/{gallery_id}", dependencies=[media_guard])
    async def api_rule34_media(
        gallery_id: int,
        n: int = 1,
        range_header: Annotated[str | None, Header(alias="Range")] = None,
    ) -> Response:
        try:
            g, _row = await _resolve(gallery_id)
        except Exception as e:
            raise HTTPException(502, f"{e.__class__.__name__}: {e}") from e
        if not g.pages:
            raise HTTPException(404, "no media for this post")
        index = max(1, min(int(n), len(g.pages))) - 1
        page = g.pages[index]
        url = page.path
        if not url:
            raise HTTPException(404, "no media url")
        client = app.source_client("rule34")
        headers = client._cdn_headers()
        headers["Range"] = range_header if range_header else "bytes=0-"
        resp = await client.transport.request("GET", url, headers=headers)
        if resp.status not in (200, 206):
            raise HTTPException(502, f"rule34 media: HTTP {resp.status}")
        ctype = (
            str(resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            or "application/octet-stream"
        )
        if not ctype.startswith(("image/", "video/")):
            ctype = f"video/{page.ext}" if page.ext in ("mp4", "webm") else f"image/{page.ext}"
        out_headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, max-age=3600",
        }
        content_range = resp.headers.get("content-range")
        if content_range:
            out_headers["Content-Range"] = content_range
        if resp.status == 206:
            out_headers["Content-Length"] = str(len(resp.content))
            return Response(
                content=resp.content,
                status_code=206,
                media_type=ctype,
                headers=out_headers,
            )
        out_headers["Content-Length"] = str(len(resp.content))
        return Response(content=resp.content, media_type=ctype, headers=out_headers)

    @router.get("/api/rule34/cover/{gallery_id}", dependencies=[media_guard])
    async def api_rule34_cover(gallery_id: int) -> Response:
        try:
            g, _row = await _resolve(gallery_id)
        except Exception as e:
            raise HTTPException(502, f"{e.__class__.__name__}: {e}") from e
        url = app.source_client("rule34").cover_url(g)
        if not url:
            raise HTTPException(404, "no cover")
        client = app.source_client("rule34")
        resp = await client.transport.request("GET", url, headers=client._cdn_headers())
        if resp.status != 200 or not resp.content:
            raise HTTPException(502, f"rule34 cover: HTTP {resp.status}")
        ctype = (
            str(resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            or "image/jpeg"
        )
        return Response(
            content=resp.content,
            media_type=ctype,
            headers={"Cache-Control": "private, max-age=3600"},
        )

    return router
