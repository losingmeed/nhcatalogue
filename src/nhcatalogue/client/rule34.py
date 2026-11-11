"""Rule 34 client — a separate provider implementation.

Normalises posts into the shared ``Gallery``/``Page``/``Tag`` models so the
catalogue, downloader, page cache and reader work without modification.

The site sits behind a JavaScript CAPTCHA and its API requires an account
key, so requests go through the app's existing transport chain (TLS
impersonation, proxy, FlareSolverr) and carry HTTP Basic auth when the user
has configured ``rule34_user_id`` / ``rule34_api_key``.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime
from typing import Any

from ..config import Settings
from .errors import BlockedError, NotFoundError
from .models import Gallery, Page, SearchResult, Tag, normalize_type
from .ratelimit import RateLimiter
from .transport import Transport

log = logging.getLogger("nhcatalogue.rule34")

RULE34_ID_OFFSET = 1_000_000_000

DEFAULT_DOMAIN = "rule34.xxx"
API_HOST = "api.rule34.xxx"

POSTS_PATH = "/index.php?page=dapi&s=post&q=index&json=1"
TAGS_PATH = "/index.php?page=dapi&s=tag&q=index&json=1"
AUTOCOMPLETE_PATH = "/autocomplete.php"

VIDEO_EXTS = {".mp4", ".webm", ".m4v", ".mov", ".swf", ".ogv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

PAGE_SIZE = 40


def encode_id(post_id: int) -> int:
    return int(post_id) + RULE34_ID_OFFSET


def decode_id(gallery_id: int) -> int:
    gid = int(gallery_id)
    return gid - RULE34_ID_OFFSET if gid >= RULE34_ID_OFFSET else gid


def is_rule34_id(gallery_id: int) -> bool:
    """Whether a catalogue id belongs to the rule34 id namespace."""
    return int(gallery_id) >= RULE34_ID_OFFSET


def _clean_ext(ext: str) -> str:
    e = (ext or "").strip().lower().lstrip(".")
    return e or "jpg"


def _is_video(ext: str) -> bool:
    return f".{_clean_ext(ext)}" in VIDEO_EXTS


def _parse_tags(raw: Any) -> list[Tag]:
    tags: list[Tag] = []
    if isinstance(raw, str):
        for name in raw.split():
            tags.append(Tag(id=0, name=name, type="general", count=0))
        return tags
    if isinstance(raw, dict):
        for type_name, names in raw.items():
            t = normalize_type(type_name)
            if not isinstance(names, (list, tuple)):
                names = [names]
            for name in names:
                if name:
                    tags.append(Tag(id=0, name=str(name), type=t, count=0))
        return tags
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, dict):
                name = str(item.get("name") or "")
                if name:
                    tags.append(
                        Tag(
                            id=int(item.get("id", 0) or 0),
                            name=name,
                            type=normalize_type(item.get("type", "general")),
                            count=int(item.get("count", 0) or 0),
                        )
                    )
            elif item:
                tags.append(Tag(id=0, name=str(item), type="general", count=0))
    return tags


def _created_timestamp(value: str) -> int:
    if not value:
        return 0
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return 0


class Rule34Client:
    def __init__(self, settings: Settings, transport: Transport):
        self.settings = settings
        self.transport = transport
        self.limiter = RateLimiter(
            settings.rate_limit_rps,
            settings.rate_limit_jitter,
            settings.host_concurrency,
        )

    def refresh_from_settings(self) -> None:
        self.limiter.update(
            self.settings.rate_limit_rps,
            self.settings.rate_limit_jitter,
            self.settings.host_concurrency,
        )

    @property
    def domain(self) -> str:
        return (self.settings.rule34_domain or DEFAULT_DOMAIN).strip() or DEFAULT_DOMAIN

    @property
    def configured(self) -> bool:
        return bool(self.settings.rule34_user_id and self.settings.rule34_api_key)

    def _auth_header(self) -> dict[str, str]:
        if not self.configured:
            return {}
        raw = f"{self.settings.rule34_user_id}:{self.settings.rule34_api_key}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Referer": f"https://{self.domain}/",
        }
        headers.update(self._auth_header())
        return headers

    async def _get_json(self, path: str) -> Any:
        url = f"https://{self.domain}{path}"
        host = self.domain
        await self.limiter.acquire(host)
        try:
            resp = await self.transport.request("GET", url, headers=self._headers())
        finally:
            self.limiter.release(host)
        if resp.status == 404:
            raise NotFoundError(path)
        if resp.status == 401 or resp.status == 403:
            raise BlockedError(
                "rule34 rejected the request — set rule34_user_id and rule34_api_key "
                "in Settings (get a key from "
                "https://rule34.xxx/index.php?page=account&s=options)"
            )
        if resp.status >= 500:
            raise BlockedError(f"rule34: HTTP {resp.status}")
        try:
            return resp.json()
        except ValueError as e:
            body = resp.content[:120].decode("utf-8", "replace")
            if "captcha" in body.lower():
                raise BlockedError(
                    "rule34 served a CAPTCHA — enable a proxy or FlareSolverr in Settings"
                ) from e
            raise BlockedError(f"rule34: non-JSON response ({body})") from e

    def _post_to_gallery(self, raw: dict[str, Any]) -> Gallery:
        post_id = int(raw.get("id", 0) or 0)
        file_info = raw.get("file") or {}
        preview = raw.get("preview") or {}
        sample = raw.get("sample") or {}

        ext = _clean_ext(str(file_info.get("ext") or "jpg"))
        kind = "video" if _is_video(ext) else "image"
        size = int(file_info.get("size") or 0)
        width = int(file_info.get("width") or 0)
        height = int(file_info.get("height") or 0)

        file_url = str(file_info.get("url") or "")
        pages: list[Page] = []
        if file_url:
            pages.append(
                Page(number=1, ext=ext, width=width, height=height, path=file_url)
            )

        siblings = raw.get("posts") or []
        for offset, sibling in enumerate(siblings):
            if not isinstance(sibling, dict):
                continue
            sib_file = sibling.get("file") or {}
            sib_url = str(sib_file.get("url") or "")
            if not sib_url:
                continue
            sib_ext = _clean_ext(str(sib_file.get("ext") or ext))
            pages.append(
                Page(
                    number=len(pages) + 1,
                    ext=sib_ext,
                    width=int(sib_file.get("width") or 0),
                    height=int(sib_file.get("height") or 0),
                    path=sib_url,
                )
            )
            if len(pages) >= 1 and offset == 0 and len(siblings) > 1:
                continue

        cover_url = str(preview.get("url") or sample.get("url") or "")
        if not cover_url and file_url and kind == "image":
            cover_url = file_url

        tags = _parse_tags(raw.get("tags"))
        title = f"post {post_id}"
        source_field = str(raw.get("source") or "").strip()
        if source_field:
            title = source_field

        return Gallery(
            id=encode_id(post_id),
            media_id=str(post_id),
            title_en=title,
            title_ja="",
            title_pretty=title,
            pages=pages,
            cover_ext=ext,
            thumb_ext=ext,
            cover_path=cover_url,
            thumb_path=cover_url,
            tag_ids=[t.id for t in tags],
            tags=tags,
            num_pages=len(pages) or 1,
            upload_date=_created_timestamp(str(raw.get("created_at") or "")),
            scanlator=str(raw.get("creator_id") or ""),
            source="rule34",
            media_kind=kind,
            file_size=size,
        )

    async def search(
        self, query: str = "", page: int = 1, sort: str = ""
    ) -> SearchResult:
        params = [f"limit={PAGE_SIZE}", f"pid={max(1, int(page))}", "fields=tag_info"]
        tags = (query or "").strip()
        if tags:
            params.append(f"tags={_quote(tags)}")
        raw = await self._get_json(f"{POSTS_PATH}&{'&'.join(params)}")
        if isinstance(raw, dict):
            raw = raw.get("posts") or []
        if not isinstance(raw, list):
            raw = []
        galleries = [self._post_to_gallery(p) for p in raw if isinstance(p, dict)]
        return SearchResult(
            galleries=galleries,
            page=max(1, int(page)),
            total_pages=0,
            per_page=PAGE_SIZE,
        )

    async def post(self, post_id: int) -> Gallery:
        raw = await self._get_json(f"{POSTS_PATH}&id={int(post_id)}")
        if isinstance(raw, list):
            raw = raw[0] if raw else {}
        if not isinstance(raw, dict) or not raw.get("id"):
            raise NotFoundError(f"rule34 post {post_id}")
        return self._post_to_gallery(raw)

    async def gallery(self, gallery_id: int) -> Gallery:
        """Catalogue-id lookup: decodes the rule34 id namespace."""
        return await self.post(decode_id(gallery_id))

    def page_url(self, gallery: Gallery, page: int) -> str:
        pages = gallery.pages
        if page < 1 or page > len(pages):
            return ""
        return pages[page - 1].path

    def cover_url(self, gallery: Gallery) -> str:
        if gallery.cover_path:
            return gallery.cover_path
        if gallery.pages:
            return gallery.pages[0].path
        return ""

    async def fetch_media(self, url: str) -> tuple[bytes, str]:
        resp = await self.transport.request("GET", url, headers=self._headers())
        if resp.status != 200 or not resp.content:
            raise BlockedError(f"rule34: HTTP {resp.status} for media")
        ctype = str(resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        return resp.content, ctype or "application/octet-stream"

    async def fetch_image(self, url: str) -> bytes:
        data, _ctype = await self.fetch_media(url)
        return data

    async def close(self) -> None:
        return None

    async def search_tags(
        self, query: str = "", type_: str = "", limit: int = 50
    ) -> list[Tag]:
        q = (query or "").strip()
        if q:
            raw = await self._get_json(f"{AUTOCOMPLETE_PATH}?q={_quote(q)}")
            names = raw if isinstance(raw, list) else []
            return [
                Tag(id=0, name=str(n).strip(), type="general", count=0)
                for n in names
                if str(n).strip()
            ][:limit]
        raw = await self._get_json(f"{TAGS_PATH}&limit={max(1, min(int(limit), 100))}")
        items = raw if isinstance(raw, list) else []
        out: list[Tag] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            out.append(
                Tag(
                    id=int(item.get("id", 0) or 0),
                    name=name,
                    type=normalize_type(item.get("type", "general")),
                    count=int(item.get("count", 0) or 0),
                )
            )
        return out[:limit]


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
