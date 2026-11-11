"""Shared fixtures: fake API responses and mock transport.

No real network calls anywhere in the test suite — everything goes through
``MockTransport`` which answers from a canned dict of URL → response.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from nhcatalogue.client.models import Tag
from nhcatalogue.client.transport import Transport, TransportResponse
from nhcatalogue.config import Settings

DEFAULT_TAGS = [
    {"id": 1, "name": "full color", "type": "tag", "count": 42000},
    {"id": 2, "name": "someartist", "type": "artist", "count": 300},
    {"id": 3, "name": "english", "type": "language", "count": 99000},
    {"id": 4, "name": "manga", "type": "category", "count": 120000},
]


def make_gallery_raw(
    gid: int,
    *,
    media_id: str = "1234",
    pages: int = 3,
    tags: list[dict] | None = None,
    title: str = "Test Gallery",
) -> dict:
    """v2 ``GET /api/v2/galleries/{id}`` detail shape (paths + full tags)."""
    if tags is None:
        tags = [dict(t) for t in DEFAULT_TAGS]
    return {
        "id": gid,
        "media_id": media_id,
        "title": {"english": title, "japanese": "テスト", "pretty": f"{title} [Digital]"},
        "cover": {
            "path": f"galleries/{media_id}/cover.jpg.webp",
            "width": 350,
            "height": 498,
        },
        "thumbnail": {
            "path": f"galleries/{media_id}/thumb.jpg.webp",
            "width": 250,
            "height": 356,
        },
        "scanlator": "",
        "pages": [
            {
                "number": i + 1,
                "path": f"galleries/{media_id}/{i + 1}.jpg",
                "width": 1280,
                "height": 1808,
                "thumbnail": f"galleries/{media_id}/{i + 1}t.jpg.webp",
            }
            for i in range(pages)
        ],
        "tags": tags,
        "num_pages": pages,
        "num_favorites": 0,
        "upload_date": 1700000000,
    }


def gallery_list_item(raw: dict) -> dict:
    """Convert a detail fixture into a v2 search-result ``GalleryListItem``."""
    media_id = str(raw.get("media_id", ""))
    title = raw.get("title") or {}
    thumb = (raw.get("thumbnail") or {}).get("path") or (
        f"galleries/{media_id}/thumb.jpg.webp"
    )
    return {
        "id": raw["id"],
        "media_id": media_id,
        "english_title": title.get("english", ""),
        "japanese_title": title.get("japanese"),
        "thumbnail": thumb,
        "thumbnail_width": 250,
        "thumbnail_height": 356,
        "num_pages": raw.get("num_pages", 0),
        "num_favorites": 0,
        "tag_ids": [t["id"] for t in raw.get("tags") or []],
        "blacklisted": False,
        "suggestion": None,
    }


def make_search_raw(galleries: list[dict], page: int = 1, pages: int = 1) -> dict:
    """v2 ``PaginatedResponse[GalleryListItem]`` shape."""
    return {
        "result": [gallery_list_item(g) for g in galleries],
        "num_pages": pages,
        "per_page": 25,
        "total": len(galleries) * pages,
    }


def make_tags_raw(tags: list[dict] | None = None) -> list[dict]:
    """v2 ``GET /api/v2/tags/ids`` payload (list of TagResponse)."""
    return [
        {
            "id": t["id"],
            "type": t["type"],
            "name": t["name"],
            "slug": t["name"].replace(" ", "-"),
            "url": f"/{t['type']}/{t['name'].replace(' ', '-')}/",
            "count": t.get("count", 0),
            "description": None,
        }
        for t in (tags if tags is not None else DEFAULT_TAGS)
    ]


CHALLENGE_BODY = b"""<html><head><title>Just a moment...</title>
<script>var cf_chl_opt ...; challenge-platform</script></head>
<body>Checking your browser before accessing. Enable JavaScript and cookies.</body></html>"""


class MockTransport(Transport):
    """Answers from ``routes`` (exact URL → TransportResponse or exception)."""

    name = "mock"

    def __init__(
        self,
        routes: Mapping[str, TransportResponse | Exception] | None = None,
        *,
        json_routes: Mapping[str, dict] | None = None,
        default: TransportResponse | Exception | None = None,
    ):
        self.routes: dict[str, TransportResponse | Exception] = dict(routes or {})
        if json_routes:
            for url, payload in json_routes.items():
                self.routes[url] = TransportResponse(
                    status=200,
                    headers={"content-type": "application/json"},
                    content=json.dumps(payload).encode(),
                )
        self.default = default
        self.requests: list[str] = []
        self.calls: list[dict] = []

    def add_json(self, url: str, payload: dict) -> None:
        self.routes[url] = TransportResponse(
            status=200, headers={"content-type": "application/json"},
            content=json.dumps(payload).encode(),
        )

    def add_text(self, url: str, body: str, content_type: str = "text/xml") -> None:
        """Register a plain-text (e.g. XML) response."""
        self.routes[url] = TransportResponse(
            status=200, headers={"content-type": content_type}, content=body.encode()
        )

    def add_empty(self, url: str, status: int = 200) -> None:
        """Register an empty-body response (what a throttled API returns)."""
        self.routes[url] = TransportResponse(status=status, headers={}, content=b"")

    def add_challenge(self, url: str) -> None:
        self.routes[url] = TransportResponse(
            status=403, headers={"cf-mitigated": "challenge"}, content=CHALLENGE_BODY
        )

    async def request(self, method, url, *, headers=None, cookies=None, json_body=None) -> TransportResponse:
        self.requests.append(url)
        self.calls.append({"method": method, "url": url, "json": json_body})
        if url in self.routes:
            hit = self.routes[url]
            if isinstance(hit, Exception):
                raise hit
            return hit
        if self.default is not None:
            if isinstance(self.default, Exception):
                raise self.default
            return self.default
        return TransportResponse(status=404, headers={}, content=b"{}")

    async def close(self) -> None:
        pass


def mk_settings(**overrides) -> Settings:
    base = dict(
        domains=["nhentai.net", "mirror.example", "mirror2.example"],
        rate_limit_rps=100.0,  # fast tests
        rate_limit_jitter=0.0,
        host_concurrency=3,
        max_retries=1,
        request_timeout=5.0,
        demote_after=3,
    )
    base.update(overrides)
    s = Settings()
    for k, v in base.items():
        setattr(s, k, v)
    return s


@pytest.fixture
def fast_settings() -> Settings:
    return mk_settings()


def make_tag(name: str, type_: str, id_: int, count: int = 10) -> Tag:
    return Tag(id=id_, name=name, type=type_, count=count)
