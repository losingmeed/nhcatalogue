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


def make_gallery_raw(
    gid: int,
    *,
    media_id: str = "1234",
    pages: int = 3,
    tags: list[dict] | None = None,
    title: str = "Test Gallery",
) -> dict:
    if tags is None:
        tags = [
            {"id": 1, "name": "full color", "type": "tag", "count": 42000},
            {"id": 2, "name": "someartist", "type": "artist", "count": 300},
            {"id": 3, "name": "english", "type": "language", "count": 99000},
            {"id": 4, "name": "manga", "type": "category", "count": 120000},
        ]
    return {
        "id": gid,
        "media_id": media_id,
        "title": {"english": title, "japanese": "テスト", "pretty": f"{title} [Digital]"},
        "images": {
            "pages": [{"t": "j", "w": 1280, "h": 1808} for _ in range(pages)],
            "cover": {"t": "j", "w": 350, "h": 500},
            "thumbnail": {"t": "j", "w": 250, "h": 354},
        },
        "tags": tags,
        "num_pages": pages,
        "upload_date": 1700000000,
    }


def make_search_raw(galleries: list[dict], page: int = 1, pages: int = 1) -> dict:
    return {"result": galleries, "page": page, "pages": pages, "per_page": 25}


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

    def add_json(self, url: str, payload: dict) -> None:
        self.routes[url] = TransportResponse(
            status=200, headers={"content-type": "application/json"},
            content=json.dumps(payload).encode(),
        )

    def add_challenge(self, url: str) -> None:
        self.routes[url] = TransportResponse(
            status=403, headers={"cf-mitigated": "challenge"}, content=CHALLENGE_BODY
        )

    async def request(self, method, url, *, headers=None, cookies=None) -> TransportResponse:
        self.requests.append(url)
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
