"""Normalized data model for nhentai API responses.

The client layer converts every raw response into these dataclasses so mirror
quirks never leak into the rest of the app (plan §1: "response normalization").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# nhentai extension codes -> real extensions
EXT_MAP = {"j": "jpg", "p": "png", "g": "gif", "w": "webp", "b": "bmp", "a": "avif"}

TAG_TYPES = (
    "general",
    "artist",
    "parody",
    "character",
    "group",
    "language",
    "category",
    "copyright",
    "series",
    "other",
)
# nhentai's API calls general tags type "tag"
API_TAG_TYPE_MAP = {"tag": "general"}
UI_TAG_TYPE_MAP = {v: k for k, v in API_TAG_TYPE_MAP.items()}

_WS_RE = re.compile(r"[\s\-_]+")
_TAG_CLEAN_RE = re.compile(r"[^\w\s\-]")


def normalize_tag_name(name: str) -> str:
    """Canonical form for tag matching: lowercase, spaces/dashes/underscores equal.

    ``"Full Color"``, ``"full-color"`` and ``"full_color"`` all normalize to
    ``"full color"`` so the tag browser and filters match across spellings.
    """
    name = _TAG_CLEAN_RE.sub("", (name or "").strip().lower())
    return _WS_RE.sub(" ", name).strip()


def normalize_type(tag_type: str) -> str:
    t = (tag_type or "").strip().lower()
    t = API_TAG_TYPE_MAP.get(t, t)
    if t not in TAG_TYPES:
        t = "general"
    return t


@dataclass
class Tag:
    id: int
    name: str
    type: str
    count: int = 0
    url: str | None = None
    source: str = "nhentai"

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Tag:
        return cls(
            id=int(raw.get("id", 0)),
            name=str(raw.get("name", "")).strip(),
            type=normalize_type(raw.get("type", "")),
            count=int(raw.get("count", 0) or 0),
            url=raw.get("url"),
        )

    @property
    def name_norm(self) -> str:
        return normalize_tag_name(self.name)

    def __str__(self) -> str:  # pragma: no cover - debug nicety
        return f"{self.type}:{self.name}"


@dataclass
class Page:
    """One page of a gallery: 1-based number, dimensions and extension.

    ``path`` is the CDN-relative path returned by the v2 API
    (``galleries/<media_id>/<n>.<ext>``); it is empty for galleries imported
    from v1-shaped metadata, in which case the URL is reconstructed from
    ``media_id`` + ``number`` + ``ext``.
    """

    number: int
    ext: str  # jpg/png/gif/webp
    width: int = 0
    height: int = 0
    path: str = ""


def ext_from_path(path: str, default: str = "jpg") -> str:
    """Real extension from a CDN path (``.../1.jpg``, ``.../cover.jpg.webp``)."""
    name = (path or "").rsplit("/", 1)[-1]
    if "." not in name:
        return default
    ext = name.rsplit(".", 1)[-1].lower()
    return "jpg" if ext == "jpeg" else (ext or default)


def sniff_image_ext(data: bytes, default: str = "jpg") -> str:
    """Detect the real image format from magic bytes.

    v2 covers are WebP even when the CDN path says ``cover.jpg.webp``, so
    naming/caching by the URL suffix alone would serve the wrong MIME type.
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"GIF":
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:2] == b"\xff\xd8":
        return "jpg"
    return default


@dataclass
class Gallery:
    id: int
    media_id: str
    title_en: str
    title_ja: str
    title_pretty: str
    pages: list[Page] = field(default_factory=list)
    cover_ext: str = "jpg"
    thumb_ext: str = "jpg"
    cover_path: str = ""
    thumb_path: str = ""
    tag_ids: list[int] = field(default_factory=list)
    tags: list[Tag] = field(default_factory=list)
    num_pages: int = 0
    upload_date: int = 0
    scanlator: str = ""
    source: str = "nhentai"
    media_kind: str = "image"
    file_size: int = 0

    @property
    def title(self) -> str:
        return self.title_pretty or self.title_en or self.title_ja or str(self.id)

    @property
    def primary_artist(self) -> str:
        for t in self.tags:
            if t.type == "artist":
                return t.name
        return "unknown"

    def tag_of_type(self, type_: str) -> list[Tag]:
        return [t for t in self.tags if t.type == normalize_type(type_)]

    @property
    def language(self) -> str:
        for t in self.tags:
            if t.type == "language":
                return t.name
        return ""

    @property
    def category(self) -> str:
        cats = self.tag_of_type("category")
        return cats[0].name if cats else ""

    def folder_vars(self) -> dict[str, str]:
        """Variables available to the folder template (plan §4)."""
        first = lambda ty: (self.tag_of_type(ty) or [None])[0]  # noqa: E731
        return {
            "artist": self.primary_artist,
            "parody": (first("parody").name if first("parody") else "unknown"),
            "character": (first("character").name if first("character") else "unknown"),
            "language": self.language or "unknown",
            "id": str(self.id),
            "title": self.title,
        }

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Gallery:
        # v2 search/list payloads are lightweight: no title object, no pages.
        if "images" not in raw and "pages" not in raw and "english_title" in raw:
            return cls.from_list_item(raw)

        title = raw.get("title") or {}
        tags = [Tag.from_api(t) for t in raw.get("tags") or []]
        raw_pages = raw.get("pages")
        cover = raw.get("cover") or {}
        thumb = raw.get("thumbnail") or {}

        if raw_pages is not None:
            # v2 detail shape: pages/cover carry CDN-relative paths
            pages = [
                Page(
                    number=int(p.get("number") or i + 1),
                    ext=ext_from_path(p.get("path", ""), default=str(p.get("ext") or "jpg")),
                    width=int(p.get("width", 0) or 0),
                    height=int(p.get("height", 0) or 0),
                    path=str(p.get("path") or ""),
                )
                for i, p in enumerate(raw_pages)
            ]
            cover_path = str(cover.get("path") or "")
            thumb_path = str(thumb.get("path") or "")
            cover_ext = (
                ext_from_path(cover_path)
                if cover_path
                else EXT_MAP.get(str(cover.get("t") or "j").lower(), "jpg")
            )
            thumb_ext = (
                ext_from_path(thumb_path)
                if thumb_path
                else EXT_MAP.get(str(thumb.get("t") or "j").lower(), "jpg")
            )
        else:
            # legacy v1 shape: images.{pages,cover,thumbnail} with extension codes
            images = raw.get("images") or {}
            pages = [
                Page(
                    number=i + 1,
                    ext=EXT_MAP.get(str(p.get("t", "j")).lower(), "jpg"),
                    width=int(p.get("w", 0) or 0),
                    height=int(p.get("h", 0) or 0),
                )
                for i, p in enumerate(images.get("pages") or [])
            ]
            cover = images.get("cover") or {}
            thumb = images.get("thumbnail") or {}
            cover_path = ""
            thumb_path = ""
            cover_ext = EXT_MAP.get(str(cover.get("t", "j")).lower(), "jpg")
            thumb_ext = EXT_MAP.get(str(thumb.get("t", "j")).lower(), "jpg")

        # mirror quirk: some mirrors omit num_pages
        num_pages = int(raw.get("num_pages") or len(pages) or 0)
        return cls(
            id=int(raw.get("id", 0)),
            media_id=str(raw.get("media_id", "")),
            title_en=str(title.get("english") or ""),
            title_ja=str(title.get("japanese") or ""),
            title_pretty=str(title.get("pretty") or ""),
            pages=pages,
            cover_ext=cover_ext,
            thumb_ext=thumb_ext,
            cover_path=cover_path,
            thumb_path=thumb_path,
            tag_ids=[t.id for t in tags],
            tags=tags,
            num_pages=num_pages,
            upload_date=int(raw.get("upload_date", 0) or 0),
            scanlator=str(raw.get("scanlator") or ""),
            source=str(raw.get("source") or "nhentai"),
            media_kind=str(raw.get("media_kind") or "image"),
            file_size=int(raw.get("file_size") or 0),
        )

    @classmethod
    def from_list_item(cls, raw: dict[str, Any]) -> Gallery:
        """v2 search result (GalleryListItem): no tags/pages, only tag ids.

        The cover path is derived from the thumbnail path so the grid can show
        a cover without a second detail request.
        """
        thumb = str(raw.get("thumbnail") or "")
        title_en = str(raw.get("english_title") or raw.get("title") or "")
        return cls(
            id=int(raw.get("id", 0)),
            media_id=str(raw.get("media_id", "")),
            title_en=title_en,
            title_ja=str(raw.get("japanese_title") or ""),
            title_pretty=title_en,
            cover_path=thumb.replace("/thumb.", "/cover.").replace("thumb.", "cover.", 1),
            thumb_path=thumb,
            cover_ext=ext_from_path(thumb),
            thumb_ext=ext_from_path(thumb),
            tag_ids=[int(i) for i in raw.get("tag_ids") or []],
            num_pages=int(raw.get("num_pages", 0) or 0),
            upload_date=int(raw.get("upload_date", 0) or 0),
        )


@dataclass
class SearchResult:
    galleries: list[Gallery]
    page: int
    total_pages: int
    per_page: int

    @classmethod
    def from_api(cls, raw: dict[str, Any], page: int) -> SearchResult:
        results = raw.get("result")
        if results is None:  # mirror quirk: some mirrors return a bare list
            results = raw if isinstance(raw, list) else []
        if isinstance(raw, list):
            page_no = page
            total_pages = 0
            per_page = 25
        else:
            # v2 uses num_pages; v1 used pages
            page_no = int(raw.get("page", page) or page)
            total_pages = int(raw.get("num_pages") or raw.get("pages") or 0)
            per_page = int(raw.get("per_page", 25) or 25)
        return cls(
            galleries=[Gallery.from_api(g) for g in results],
            page=page_no,
            total_pages=total_pages,
            per_page=per_page,
        )
