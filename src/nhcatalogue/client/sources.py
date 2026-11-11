"""Registry of gallery providers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceSpec:
    name: str
    label: str
    icon: str
    is_comic: bool
    has_video: bool
    tag_types: tuple[str, ...]


NHENTAI = SourceSpec(
    name="nhentai",
    label="Nhentai",
    icon="📚",
    is_comic=True,
    has_video=False,
    tag_types=(
        "general",
        "artist",
        "parody",
        "character",
        "group",
        "language",
        "category",
    ),
)

RULE34 = SourceSpec(
    name="rule34",
    label="Rule 34",
    icon="🖼️",
    is_comic=False,
    has_video=True,
    tag_types=(
        "general",
        "artist",
        "copyright",
        "character",
        "series",
        "other",
    ),
)

SOURCES: dict[str, SourceSpec] = {NHENTAI.name: NHENTAI, RULE34.name: RULE34}


def get_source(name: str) -> SourceSpec:
    return SOURCES.get((name or "").strip().lower(), NHENTAI)
