"""Rule 34 client — a separate provider implementation.

Normalises posts into the shared ``Gallery``/``Page``/``Tag`` models so the
catalogue, downloader, page cache and reader work without modification.

Three hosts are involved and they are *not* interchangeable:

* ``rule34.xxx`` — the site (age gate, HTML pages, the account page where the
  API key is generated).
* ``api.rule34.xxx`` — the dapi. It is the only host that answers the
  ``page=dapi`` routes, and it needs the account id and API key from the
  site's *Account → Options → API Access Credentials* field. The credentials
  travel as the ``api_key`` / ``user_id`` **query parameters** on every
  request; HTTP Basic auth is not accepted by this endpoint.
* ``api-cdn.rule34.xxx`` (images) and ``api-cdn-mp4.rule34.xxx`` (videos) —
  these serve the media. The URLs the API returns already point at the right
  CDN host, so they are used verbatim; rewriting them to a ``wimg.`` host (as
  some third-party tools do) yields 403s, because that host does not exist
  for this deployment.

Search syntax
-------------
The dapi matches **plain tag names only** — ``artist:foo`` returns nothing,
even though the site itself understands the prefix. Tags are separated by a
space (AND) and a leading ``-`` negates (``foo -bar``). Because a rule34 tag
name belongs to exactly one namespace, the plain name is unambiguous. The
client still accepts prefixed input and strips the prefix, so a query typed
against the website's syntax keeps working.

Everything goes through the app's existing transport chain, so TLS
impersonation clears the site's JavaScript CAPTCHA.
"""

from __future__ import annotations

import base64
import logging
import re
import time
from collections import OrderedDict
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlencode

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

# Tag namespaces the dapi reports in ``tag_info`` entries.
# "tag" is the site's name for the general namespace.
KNOWN_TAG_TYPES = {
    "tag",
    "general",
    "artist",
    "copyright",
    "character",
    "series",
    "metadata",
    "other",
    "system",
    "meta",
}

PAGE_SIZE = 40

# In-process response cache for live searches (client-side twin of the
# server's LiveCache — lives in the long-lived client instance).
RULE34_SEARCH_CACHE_TTL = 300.0
RULE34_SEARCH_CACHE_MAXSIZE = 128

_TAG_XML_RE = re.compile(r"<tag\b[^>]*/>")
_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
_QUOTED_RE = re.compile(r'^"(.*)"$')


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


def _tag_type_from_code(code: str | None) -> str:
    """Map the dapi's numeric tag namespace onto our tag types.

    The ``s=tag`` endpoint encodes the namespace as an integer:
    ``0`` general, ``1`` artist, ``3`` copyright, ``4`` character,
    ``5`` metadata. (``series`` has no separate code — rule34 files series
    tags under copyright, e.g. ``taimanin_(series)``.)
    """
    return {
        "0": "general",
        "1": "artist",
        "3": "copyright",
        "4": "character",
        "5": "metadata",
    }.get(str(code or "").strip(), "general")


def _normalize_rule34_type(type_: str | None) -> str:
    """Map a rule34 namespace name onto our tag types.

    Rule 34 reports the general namespace as ``tag`` and the AI-related one
    as ``metadata``; the shared ``normalize_type`` would fold ``metadata``
    into ``general``, so it is mapped to ``other`` here to keep the two
    apart in the tag browser.
    """
    t = (type_ or "").strip().lower()
    if t in ("tag", "general", ""):
        return "general"
    if t == "metadata":
        return "other"
    return normalize_type(t)


def _parse_xml_tags(xml: str) -> list[Tag]:
    """Parse the dapi's XML tag list (``s=tag&q=index``).

    That endpoint answers XML even when ``json=1`` is requested.
    """
    out: list[Tag] = []
    for raw in _TAG_XML_RE.findall(xml or ""):
        attrs = dict(_ATTR_RE.findall(raw))
        name = (attrs.get("name") or "").strip()
        if not name:
            continue
        out.append(
            Tag(
                id=int(attrs.get("id") or 0),
                name=name,
                type=_tag_type_from_code(attrs.get("type")),
                count=int(attrs.get("count") or 0),
            )
        )
    return out


def _parse_tags(raw: Any) -> list[Tag]:
    """Parse the dapi's tag payload into ``Tag`` objects.

    Two shapes occur:

    * a plain space-separated string (``"1girl solo smile"``) — the default
      ``tags`` field. Names carry no namespace prefix here.
    * a list of ``{"count", "type", "tag"}`` dicts — the ``tag_info`` field,
      which the API only returns when ``fields=tag_info`` is requested. This
      is the only source of per-tag type and post counts.
    """
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
                # tag_info entries use "tag" for the name, not "name".
                name = str(item.get("tag") or item.get("name") or "").strip()
                if not name:
                    continue
                tags.append(
                    Tag(
                        id=int(item.get("id", 0) or 0),
                        name=name,
                        type=_normalize_rule34_type(item.get("type", "general")),
                        count=int(item.get("count", 0) or 0),
                    )
                )
            elif item:
                tags.append(Tag(id=0, name=str(item), type="general", count=0))
    return tags


def _created_timestamp(value: Any) -> int:
    """Parse the dapi's date fields.

    The XML flavour uses ``Wed Nov 11 14:05:45 +0100 2026``; the JSON flavour
    omits ``created_at`` entirely and exposes only ``change`` (a Unix
    timestamp with the same value). Both are accepted here.
    """
    if value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    for fmt in (
        "%a %b %d %H:%M:%S %z %Y",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
    ):
        try:
            return int(datetime.strptime(text, fmt).timestamp())
        except ValueError:
            continue
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def split_query_tokens(query: str) -> list[str]:
    """Split a tag query into tokens, keeping quoted phrases together."""
    out: list[str] = []
    cur = ""
    quoted = False
    for ch in query or "":
        if ch == '"':
            quoted = not quoted
            cur += ch
        elif ch.isspace() and not quoted:
            if cur:
                out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def normalize_tag_query(query: str) -> str:
    """Rewrite a tag query into the form the dapi actually understands.

    The dapi matches plain tag names; a ``namespace:`` prefix makes the whole
    token unmatchable. The prefix is therefore stripped, while the ``-``
    negation marker is kept. Surrounding quotes are dropped as well — the
    dapi does not treat a quoted name as a phrase.
    """
    tokens: list[str] = []
    for token in split_query_tokens(query):
        negated = token.startswith("-")
        body = token[1:] if negated else token
        prefix, sep, rest = body.partition(":")
        if sep and prefix.lower() in KNOWN_TAG_TYPES and rest:
            body = rest
        body = body.strip()
        quoted = _QUOTED_RE.match(body)
        if quoted:
            body = quoted.group(1)
        if not body:
            continue
        tokens.append(("-" if negated else "") + body)
    return " ".join(tokens)


def split_credentials(api_key: str, user_id: str) -> tuple[str, str]:
    """Extract the account id and API key from the stored settings.

    Rule 34 shows the credentials as a single blob in the account page —
    ``&api_key=<key>&user_id=<id>`` — and it is natural to paste that whole
    thing into the "api key" field. The dapi, however, compares the api_key
    parameter verbatim, so the blob has to be unpacked before it is sent.

    Returns ``(user_id, api_key)``; both are empty when nothing is set.
    """
    key = (api_key or "").strip()
    uid = (user_id or "").strip()
    if "api_key=" in key:
        parsed = parse_qs(key.lstrip("&"))
        values = parsed.get("api_key") or []
        if values:
            key = values[0].strip()
        if not uid:
            ids = parsed.get("user_id") or []
            if ids:
                uid = ids[0].strip()
    return uid, key


class Rule34Client:
    def __init__(self, settings: Settings, transport: Transport):
        self.settings = settings
        self.transport = transport
        # The dapi is a separate host with its own limits, so it gets its
        # own rate limiter rather than sharing the nhentai CDN one.
        self.limiter = RateLimiter(
            settings.rule34_rate_limit_rps,
            settings.rate_limit_jitter,
            settings.host_concurrency,
        )
        # Tag lookups are slow (one rate-limited request per name), so the
        # answers are cached: a tag's namespace and count never change.
        self._tag_cache: dict[str, Tag] = {}
        # In-process live search response cache (client-side twin of the
        # server's LiveCache — lives in the long-lived client instance).
        self._search_cache: OrderedDict[str, tuple[float, SearchResult]] = OrderedDict()
        # The tag endpoint is cheap and tolerates parallel requests (ten
        # lookups answer in ~0.2s), so it gets a faster limiter than the
        # main search endpoint — otherwise the tag picker would take
        # several seconds to fill.
        self._tag_limiter = RateLimiter(
            settings.rule34_tag_lookup_rps,
            settings.rate_limit_jitter,
            settings.host_concurrency,
        )

    def refresh_from_settings(self) -> None:
        self.limiter.update(
            self.settings.rule34_rate_limit_rps,
            self.settings.rate_limit_jitter,
            self.settings.host_concurrency,
        )
        self._tag_limiter.update(
            self.settings.rule34_tag_lookup_rps,
            self.settings.rate_limit_jitter,
            self.settings.host_concurrency,
        )

    @property
    def domain(self) -> str:
        """The site host (referer, HTML pages)."""
        return (self.settings.rule34_domain or DEFAULT_DOMAIN).strip() or DEFAULT_DOMAIN

    @property
    def api_host(self) -> str:
        """The dapi host — separate from the site host."""
        return (self.settings.rule34_api_host or DEFAULT_API_HOST).strip() or DEFAULT_API_HOST

    @property
    def credentials(self) -> tuple[str, str]:
        """The account id and API key, unpacked from whatever was pasted."""
        return split_credentials(
            self.settings.rule34_api_key, self.settings.rule34_user_id
        )

    @property
    def configured(self) -> bool:
        uid, key = self.credentials
        return bool(uid and key)

    def _auth_params(self) -> dict[str, str]:
        """Credentials as query parameters — the only form the dapi accepts."""
        uid, key = self.credentials
        if not (uid and key):
            return {}
        return {"api_key": key, "user_id": uid}

    def _headers(self) -> dict[str, str]:
        """Headers for API requests (the credentials are in the query string)."""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Referer": f"https://{self.domain}/",
        }
        if self.configured:
            # The dapi reads the credentials from the query string; the
            # Authorization header is kept as a belt-and-braces fallback for
            # deployments that proxy the API behind a Basic-auth gateway.
            uid, key = self.credentials
            raw = f"{uid}:{key}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
        return headers

    def _cdn_headers(self) -> dict[str, str]:
        """Headers for CDN requests.

        The media host is public, so the account credentials are deliberately
        *not* sent there.
        """
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "image/*,video/*,*/*",
            "Referer": f"https://{self.domain}/",
        }

    def _build_url(
        self, path: str, params: dict[str, Any] | None = None, *, keep_empty: str = ""
    ) -> str:
        """Build a dapi URL.

        ``keep_empty`` names a parameter whose empty value is meaningful and
        must survive — the autocomplete endpoint returns the site's most
        popular tags for ``?q=`` but nothing at all for a missing ``q``.
        """
        query = {
            k: v
            for k, v in (params or {}).items()
            if v is not None and (v != "" or k == keep_empty)
        }
        query.update(self._auth_params())
        url = f"https://{self.api_host}{path}"
        if query:
            url += ("&" if "?" in url else "?") + urlencode(query)
        return url

    async def _request(self, url: str, *, empty_is_none: bool = False) -> Any:
        """GET a dapi URL and return the decoded payload.

        The dapi answers a query that matches **nothing** with an empty body
        (``200`` + zero bytes) in JSON mode — that is a valid, empty result
        set, not an error. Genuine failures come back as a JSON document
        (``{"success": false, ...}``) or an HTTP error status.

        ``empty_is_none`` is used by the single-post lookup, where an empty
        body means "no such post" rather than "no matches".
        """
        host = self.api_host
        await self.limiter.acquire(host)
        try:
            resp = await self.transport.request("GET", url, headers=self._headers())
        finally:
            self.limiter.release(host)

        if resp.status == 404:
            raise NotFoundError(url)
        if resp.status in (401, 403):
            raise BlockedError(
                "rule34 rejected the request — set rule34_user_id and "
                "rule34_api_key in Settings (get a key from "
                "https://rule34.xxx/index.php?page=account&s=options)"
            )
        if resp.status >= 500:
            raise BlockedError(f"rule34: HTTP {resp.status}")

        body = resp.content or b""
        if not body.strip():
            # No matches.
            if empty_is_none:
                return None
            return []

        ctype = str(resp.headers.get("content-type") or "").lower()
        if "json" not in ctype and resp.text.lstrip()[:1] not in "[{":
            # XML response (the tag endpoint always answers XML).
            return resp.text
        try:
            payload = resp.json()
        except ValueError as e:
            text = resp.text[:200]
            if "captcha" in text.lower():
                raise BlockedError(
                    "rule34 served a CAPTCHA — enable a proxy or FlareSolverr in Settings"
                ) from e
            raise BlockedError(f"rule34: non-JSON response ({text})") from e
        # The api answers errors with a bare string (json mode), an "error"
        # key, or {"success": false, "message": ...} when the search dies.
        if isinstance(payload, str):
            raise BlockedError(f"rule34: {payload}")
        if isinstance(payload, dict):
            if payload.get("error"):
                raise BlockedError(f"rule34: {payload['error']}")
            if payload.get("success") is False:
                raise BlockedError(
                    f"rule34: {payload.get('message') or 'the search backend is down'}"
                )
        return payload

    async def _get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        empty_is_none: bool = False,
        keep_empty: str = "",
    ) -> Any:
        return await self._request(
            self._build_url(path, params, keep_empty=keep_empty),
            empty_is_none=empty_is_none,
        )

    def _post_to_gallery(self, raw: dict[str, Any]) -> Gallery:
        post_id = int(raw.get("id", 0) or 0)

        file_url = str(raw.get("file_url") or "")
        ext = _clean_ext(_ext_from_url(file_url) or "jpg")
        kind = "video" if _is_video(ext) else "image"
        width = int(raw.get("width") or 0)
        height = int(raw.get("height") or 0)

        pages: list[Page] = []
        if file_url:
            pages.append(Page(number=1, ext=ext, width=width, height=height, path=file_url))

        # Cover: the small thumbnail the API offers; fall back to the sample
        # and finally to the media itself.
        cover_url = str(raw.get("preview_url") or "")
        if not cover_url:
            cover_url = str(raw.get("sample_url") or "")
        if not cover_url:
            cover_url = file_url

        # tag_info carries the namespace and per-tag post count; the plain
        # "tags" string does not. Prefer tag_info when it is present.
        tag_info = raw.get("tag_info")
        tags = _parse_tags(tag_info if tag_info else raw.get("tags"))

        # A rule34 post has no title; the "source" field is the URL the
        # artist posted it to, which is the closest thing to one.
        title = str(raw.get("source") or "").strip() or f"post {post_id}"
        creator = str(raw.get("owner") or raw.get("creator_id") or "")

        upload_date = _created_timestamp(raw.get("created_at"))
        if not upload_date:
            upload_date = _created_timestamp(raw.get("change"))

        return Gallery(
            id=encode_id(post_id),
            media_id=str(post_id),
            title_en=title,
            title_ja="",
            title_pretty=title,
            pages=pages,
            cover_ext=_clean_ext(_ext_from_url(cover_url) or ext),
            thumb_ext=_clean_ext(_ext_from_url(cover_url) or ext),
            cover_path=cover_url,
            thumb_path=cover_url,
            tag_ids=[t.id for t in tags],
            tags=tags,
            num_pages=len(pages) or 1,
            upload_date=upload_date,
            scanlator=creator,
            source="rule34",
            media_kind=kind,
            file_size=0,
        )

    async def search(
        self, query: str = "", page: int = 1, sort: str = "", match: str = "and"
    ) -> SearchResult:
        """Search the dapi.

        ``match="and"`` (default) requires every tag in the query to be
        present; ``match="or"`` unions the results of the individual tags.
        """
        params: dict[str, Any] = {
            "json": 1,
            "limit": PAGE_SIZE,
            "pid": max(1, int(page)),
            # Without this the API omits tag_info, and we lose the tag
            # namespaces and counts.
            "fields": "tag_info",
        }
        tags = normalize_tag_query(query or "")
        if match not in ("and", "or"):
            match = "and"
        if tags and match == "and":
            params["tags"] = tags
        cache_key = f"q={query}|p={page}|s={sort}|m={match}"
        cached = self._search_cache.get(cache_key)
        if cached is not None and time.monotonic() - cached[0] <= RULE34_SEARCH_CACHE_TTL:
            self._search_cache.move_to_end(cache_key)
            return cached[1]
        if match == "or" and tags:
            result = await self._search_or(tags, page=page)
        else:
            if tags:
                params["tags"] = tags
            raw = await self._get_json(POSTS_PATH, params)
            if isinstance(raw, dict):
                raw = raw.get("posts") or raw.get("result") or []
            if not isinstance(raw, list):
                raw = []
            galleries = [self._post_to_gallery(p) for p in raw if isinstance(p, dict)]
            result = SearchResult(
                galleries=galleries,
                page=max(1, int(page)),
                total_pages=0,
                per_page=PAGE_SIZE,
            )
        self._search_cache[cache_key] = (time.monotonic(), result)
        while len(self._search_cache) > RULE34_SEARCH_CACHE_MAXSIZE:
            self._search_cache.popitem(last=False)
        return result

    async def _search_or(self, tags: str, page: int = 1) -> SearchResult:
        """OR-mode search: union the result sets of the individual tags."""
        merged: dict[int, Gallery] = {}
        found_any = False
        for tag in [t for t in (tags or "").split() if t and not t.startswith("-")]:
            found_any = True
            sub = await self.search(tag, page=page)
            for g in sub.galleries:
                merged[g.id] = g
        if not found_any:
            params: dict[str, Any] = {
                "json": 1,
                "limit": PAGE_SIZE,
                "pid": max(1, int(page)),
                "fields": "tag_info",
            }
            raw = await self._get_json(POSTS_PATH, params)
            if isinstance(raw, dict):
                raw = raw.get("posts") or raw.get("result") or []
            galleries = [self._post_to_gallery(p) for p in raw if isinstance(p, dict)] if isinstance(raw, list) else []
            return SearchResult(
                galleries=galleries,
                page=max(1, int(page)),
                total_pages=0,
                per_page=PAGE_SIZE,
            )
        found = sorted(merged.values(), key=lambda g: g.upload_date, reverse=True)
        return SearchResult(
            galleries=found,
            page=max(1, int(page)),
            total_pages=0,
            per_page=max(1, len(found)),
        )

    async def post(self, post_id: int) -> Gallery:
        raw = await self._get_json(
            POSTS_PATH,
            {"json": 1, "id": int(post_id), "fields": "tag_info"},
            empty_is_none=True,
        )
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
        resp = await self.transport.request("GET", url, headers=self._cdn_headers())
        if resp.status != 200 or not resp.content:
            raise BlockedError(f"rule34: HTTP {resp.status} for media")
        ctype = str(resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        return resp.content, ctype or "application/octet-stream"

    async def fetch_image(self, url: str) -> bytes:
        data, _ctype = await self.fetch_media(url)
        return data

    async def media_size(self, url: str) -> int:
        """Size of a media file in bytes, via a HEAD request.

        The dapi does not report file sizes, so this is the only way to warn
        about a large download before it starts. Returns 0 when unknown.
        """
        if not url:
            return 0
        try:
            resp = await self.transport.request("HEAD", url, headers=self._cdn_headers())
        except Exception as e:  # pragma: no cover - network hiccup
            log.debug("rule34: HEAD %s failed: %s", url, e)
            return 0
        if resp.status >= 400:
            return 0
        raw = resp.headers.get("content-length")
        try:
            return int(raw) if raw else 0
        except (TypeError, ValueError):
            return 0

    async def close(self) -> None:
        return None

    async def _lookup_tag(self, name: str) -> Tag | None:
        """Look up one tag by exact name.

        The ``s=tag`` endpoint accepts a single ``name=`` and answers with
        the tag's real post count and namespace code. Results are cached —
        a tag's namespace and count do not change.
        """
        cached = self._tag_cache.get(name)
        if cached is not None:
            return cached
        url = self._build_url(TAGS_PATH, {"name": name})
        host = self.api_host
        await self._tag_limiter.acquire(host)
        try:
            resp = await self.transport.request("GET", url, headers=self._headers())
        finally:
            self._tag_limiter.release(host)
        if resp.status >= 400 or not (resp.content or b"").strip():
            return None
        raw = resp.text
        for tag in _parse_xml_tags(raw):
            if tag.name == name:
                self._tag_cache[name] = tag
                return tag
        return None

    async def search_tags(self, query: str = "", type_: str = "", limit: int = 50) -> list[Tag]:
        """Search rule34 tags.

        The autocomplete endpoint is the usable one: it answers
        ``[{"label": "solo (4401302)", "value": "solo"}, ...]`` — the label
        carries the post count, and with an empty query it returns the site's
        most popular tags. It needs no credentials.

        The ``s=tag`` endpoint is *not* usable for browsing: it enumerates the
        tag table from the oldest entry, so its first page holds obscure tags
        with single-digit counts rather than the popular ones. It is, however,
        the only way to learn a tag's namespace, so it is used for exact
        lookups.
        """
        q = (query or "").strip()
        raw = await self._get_json(AUTOCOMPLETE_PATH, {"q": q}, keep_empty="q")
        items = raw if isinstance(raw, list) else []
        out: list[Tag] = []
        for item in items:
            if isinstance(item, dict):
                name = str(item.get("value") or "").strip()
                count = 0
                label = str(item.get("label") or "")
                if label.endswith(")") and "(" in label:
                    try:
                        count = int(label.rsplit("(", 1)[1].rstrip(")"))
                    except ValueError:
                        count = 0
            else:
                name = str(item or "").strip()
                count = 0
            if not name:
                continue
            out.append(Tag(id=0, name=name, type="general", count=count))
        return out[:limit]
