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

TAG_TYPES = ("general", "artist", "parody", "character", "group", "language", "category")
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
    """One page of a gallery: 1-based number, dimensions and extension."""

    number: int
    ext: str  # jpg/png/gif/webp
    width: int = 0
    height: int = 0


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
    tags: list[Tag] = field(default_factory=list)
    num_pages: int = 0
    upload_date: int = 0
    scanlator: str = ""

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
        images = raw.get("images") or {}
        raw_pages = images.get("pages") or []
        pages = [
            Page(
                number=i + 1,
                ext=EXT_MAP.get(str(p.get("t", "j")).lower(), "jpg"),
                width=int(p.get("w", 0) or 0),
                height=int(p.get("h", 0) or 0),
            )
            for i, p in enumerate(raw_pages)
        ]
        title = raw.get("title") or {}
        tags = [Tag.from_api(t) for t in raw.get("tags") or []]
        # mirror quirk: some mirrors omit num_pages
        num_pages = int(raw.get("num_pages") or len(pages) or 0)
        cover = images.get("cover") or {}
        thumb = images.get("thumbnail") or {}
        g = cls(
            id=int(raw.get("id", 0)),
            media_id=str(raw.get("media_id", "")),
            title_en=str(title.get("english") or ""),
            title_ja=str(title.get("japanese") or ""),
            title_pretty=str(title.get("pretty") or ""),
            pages=pages,
            cover_ext=EXT_MAP.get(str(cover.get("t", "j")).lower(), "jpg"),
            thumb_ext=EXT_MAP.get(str(thumb.get("t", "j")).lower(), "jpg"),
            tags=tags,
            num_pages=num_pages,
            upload_date=int(raw.get("upload_date", 0) or 0),
            scanlator=str(raw.get("scanlator") or ""),
        )
        return g


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
        return cls(
            galleries=[Gallery.from_api(g) for g in results],
            page=int(raw.get("page", page) if not isinstance(raw, list) else page),
            total_pages=int(raw.get("pages", 0) if not isinstance(raw, list) else 0),
            per_page=int(raw.get("per_page", 25) if not isinstance(raw, list) else 25),
        )
