"""Rule 34 client — a separate provider implementation.

Normalises posts into the shared ``Gallery``/``Page``/``Tag`` models so the
catalogue, downloader, page cache and reader work without modification.

Two hosts are involved: the site (``rule34.xxx``) and the API
(``api.rule34.xxx``). The API needs the account id and API key from the
site's *Account → Options → API Access Credentials* field, and they travel
as the ``user_id`` / ``api_key`` query parameters on every request. Requests
go through the app's existing transport chain, so TLS impersonation clears
the site's JavaScript CAPTCHA.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from ..config import Settings
from .errors import BlockedError, NotFoundError
from .models import Gallery, Page, SearchResult, Tag, normalize_type
from .ratelimit import RateLimiter
from .transport import Transport

log = logging.getLogger("nhcatalogue.rule34")

RULE34_ID_OFFSET = 1_000_000_000

DEFAULT_DOMAIN = "rule34.xxx"
DEFAULT_API_HOST = "api.rule34.xxx"

POSTS_PATH = "/index.php?page=dapi&s=post&q=index"
TAGS_PATH = "/index.php?page=dapi&s=tag&q=index"
AUTOCOMPLETE_PATH = "/autocomplete.php"

VIDEO_EXTS = {".mp4", ".webm", ".m4v", ".mov", ".swf", ".ogv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

# namespaces rule34 uses when typed tags are requested
KNOWN_TAG_TYPES = {
    "artist",
    "copyright",
    "character",
    "series",
    "general",
    "other",
    "metadata",
    "system",
    "meta",
    "tag",
}

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


def _ext_from_url(url: str) -> str:
    name = (url or "").split("?")[0].rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def cdn_url(url: str) -> str:
    """Rule 34 serves images from a ``wimg.`` host; videos from the site host."""
    if not url.startswith("http"):
        return url
    scheme, _, rest = url.partition("://")
    host, _, path = rest.partition("/")
    if host.startswith("wimg.") or host.startswith("api."):
        return url
    # "rule34.xxx" → "wimg.xxx" (the site's own host convention)
    parts = host.split(".")
    if len(parts) >= 2:
        host = "wimg." + ".".join(parts[1:])
    else:
        host = "wimg." + host
    return f"{scheme}://{host}/{path}"


def _parse_tags(raw: Any) -> list[Tag]:
    tags: list[Tag] = []
    if isinstance(raw, str):
        # the api returns a space-separated list; names may carry a namespace
        # prefix ("artist:someartist") when typed tags are requested
        for token in raw.split():
            name = token
            type_ = "general"
            if ":" in token:
                prefix, _, rest = token.partition(":")
                if prefix.lower() in KNOWN_TAG_TYPES and rest:
                    type_ = normalize_type(prefix)
                    name = rest
            tags.append(Tag(id=0, name=name, type=type_, count=0))
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
    """The API uses ``Wed Oct 22 09:14:33 +0000 2025``; be tolerant of ISO too."""
    if not value:
        return 0
    text = str(value).strip()
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return int(datetime.strptime(text, fmt).timestamp())
        except ValueError:
            continue
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
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
        """The site host (referer, media)."""
        return (self.settings.rule34_domain or DEFAULT_DOMAIN).strip() or DEFAULT_DOMAIN

    @property
    def api_host(self) -> str:
        """The API host — separate from the site host."""
        return (self.settings.rule34_api_host or DEFAULT_API_HOST).strip() or DEFAULT_API_HOST

    @property
    def configured(self) -> bool:
        return bool(self.settings.rule34_user_id and self.settings.rule34_api_key)

    def _auth_params(self) -> dict[str, str]:
        if not self.configured:
            return {}
        return {
            "api_key": self.settings.rule34_api_key,
            "user_id": str(self.settings.rule34_user_id),
        }

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Referer": f"https://{self.domain}/",
        }
        if self.configured:
            raw = f"{self.settings.rule34_user_id}:{self.settings.rule34_api_key}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
            headers["X-Api-Key"] = self.settings.rule34_api_key
            headers["X-User-Id"] = str(self.settings.rule34_user_id)
        return headers

    def _cookies(self) -> dict[str, str]:
        if not self.configured:
            return {}
        return {
            "api_key": self.settings.rule34_api_key,
            "user_id": str(self.settings.rule34_user_id),
        }

    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        query.update(self._auth_params())
        url = f"https://{self.api_host}{path}"
        if query:
            url += ("&" if "?" in url else "?") + urlencode(query)
        host = self.api_host
        await self.limiter.acquire(host)
        try:
            resp = await self.transport.request(
                "GET",
                url,
                headers=self._headers(),
                cookies=self._cookies(),
            )
        finally:
            self.limiter.release(host)
        if resp.status == 404:
            raise NotFoundError(path)
        if resp.status in (401, 403):
            raise BlockedError(
                "rule34 rejected the request — set rule34_user_id and rule34_api_key "
                "in Settings (get a key from "
                "https://rule34.xxx/index.php?page=account&s=options)"
            )
        if resp.status >= 500:
            raise BlockedError(f"rule34: HTTP {resp.status}")
        try:
            payload = resp.json()
        except ValueError as e:
            body = resp.content[:200].decode("utf-8", "replace")
            if "captcha" in body.lower():
                raise BlockedError(
                    "rule34 served a CAPTCHA — enable a proxy or FlareSolverr in Settings"
                ) from e
            raise BlockedError(f"rule34: non-JSON response ({body})") from e
        # the api answers errors with a bare string (json mode) or {"error": ...}
        if isinstance(payload, str):
            raise BlockedError(f"rule34: {payload}")
        if isinstance(payload, dict) and payload.get("error"):
            raise BlockedError(f"rule34: {payload['error']}")
        return payload

    def _post_to_gallery(self, raw: dict[str, Any]) -> Gallery:
        post_id = int(raw.get("id", 0) or 0)

        file_info = raw.get("file") or {}
        file_url = str(raw.get("file_url") or file_info.get("url") or "")
        ext = _clean_ext(str(file_info.get("ext") or _ext_from_url(file_url) or "jpg"))
        kind = "video" if _is_video(ext) else "image"
        size = int(raw.get("file_size") or file_info.get("size") or 0)
        width = int(raw.get("width") or file_info.get("width") or 0)
        height = int(raw.get("height") or file_info.get("height") or 0)

        pages: list[Page] = []
        if file_url:
            pages.append(
                Page(
                    number=1,
                    ext=ext,
                    width=width,
                    height=height,
                    path=cdn_url(file_url),
                )
            )

        siblings = raw.get("posts") or []
        for sibling in siblings:
            if not isinstance(sibling, dict):
                continue
            sib_file = sibling.get("file") or {}
            sib_url = str(sibling.get("file_url") or sib_file.get("url") or "")
            if not sib_url:
                continue
            sib_ext = _clean_ext(str(sib_file.get("ext") or _ext_from_url(sib_url) or ext))
            pages.append(
                Page(
                    number=len(pages) + 1,
                    ext=sib_ext,
                    width=int(sibling.get("width") or sib_file.get("width") or 0),
                    height=int(sibling.get("height") or sib_file.get("height") or 0),
                    path=cdn_url(sib_url),
                )
            )

        cover_url = str(raw.get("preview_url") or raw.get("sample_url") or "")
        if not cover_url:
            preview = raw.get("preview") or {}
            cover_url = str(preview.get("url") or "")
        if not cover_url and file_url and kind == "image":
            cover_url = file_url
        if cover_url:
            cover_url = cdn_url(cover_url)

        tags = _parse_tags(raw.get("tags"))
        title = str(raw.get("source") or "").strip() or f"post {post_id}"
        creator = raw.get("creator_name") or raw.get("author") or raw.get("creator_id") or ""

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
            scanlator=str(creator),
            source="rule34",
            media_kind=kind,
            file_size=size,
        )

    async def search(
        self, query: str = "", page: int = 1, sort: str = ""
    ) -> SearchResult:
        params: dict[str, Any] = {
            "json": 1,
            "limit": PAGE_SIZE,
            "pid": max(1, int(page)),
            "fields": "tag_info",
        }
        tags = (query or "").strip()
        if tags:
            params["tags"] = tags
        raw = await self._get_json(POSTS_PATH, params)
        if isinstance(raw, dict):
            raw = raw.get("posts") or raw.get("result") or []
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
        raw = await self._get_json(POSTS_PATH, {"json": 1, "id": int(post_id)})
        if isinstance(raw, list):
            raw = raw[0] if raw else {}
        if isinstance(raw, dict) and "posts" in raw:
            posts = raw.get("posts") or []
            raw = posts[0] if posts else {}
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
        resp = await self.transport.request(
            "GET", url, headers=self._headers(), cookies=self._cookies()
        )
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
            raw = await self._get_json(AUTOCOMPLETE_PATH, {"q": q})
            names = raw if isinstance(raw, list) else []
            return [
                Tag(id=0, name=str(n).strip(), type="general", count=0)
                for n in names
                if str(n).strip()
            ][:limit]
        raw = await self._get_json(TAGS_PATH, {"json": 1, "limit": max(1, min(int(limit), 100))})
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
