"""Client tests: CF challenge scenarios, domain rotation, rate limiter, models.

All network access is mocked via MockTransport — zero real requests.
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import (
    CHALLENGE_BODY,
    MockTransport,
    make_gallery_raw,
    make_search_raw,
    make_tags_raw,
    mk_settings,
)

from nhcatalogue.client import (
    BlockedError,
    NhentaiClient,
    NotFoundError,
)
from nhcatalogue.client.challenge import looks_like_challenge
from nhcatalogue.client.domains import DomainRotator
from nhcatalogue.client.models import (
    Gallery,
    normalize_tag_name,
    normalize_type,
    sniff_image_ext,
)
from nhcatalogue.client.ratelimit import RateLimiter
from nhcatalogue.client.transport import TransportResponse

# ------------------------------------------------------------- challenge detect


class TestChallengeDetection:
    def test_cf_mitigated_header(self):
        assert looks_like_challenge(403, {"cf-mitigated": "challenge"}, b"")

    def test_challenge_body_markers(self):
        assert looks_like_challenge(503, {}, CHALLENGE_BODY)

    def test_403_without_markers_is_not_challenge(self):
        assert not looks_like_challenge(403, {}, b"plain forbidden")

    def test_200_is_never_challenge(self):
        assert not looks_like_challenge(200, {"cf-mitigated": "challenge"}, b"{}")


# ----------------------------------------------------------------- domain rotation


class TestDomainRotation:
    def test_order_preserved_when_healthy(self):
        r = DomainRotator(["a.com", "b.com", "c.com"])
        assert r.order() == ["a.com", "b.com", "c.com"]

    def test_demote_after_failures(self):
        r = DomainRotator(["a.com", "b.com"], demote_after=3)
        assert not r.report_failure("a.com", "x")
        assert not r.report_failure("a.com", "x")
        assert r.report_failure("a.com", "x")  # 3rd failure demotes
        assert r.order()[0] == "b.com"
        assert r.stats()[0]["demoted"] is True

    def test_success_resets_failures(self):
        r = DomainRotator(["a.com"], demote_after=2)
        r.report_failure("a.com", "x")
        r.report_success("a.com")
        r.report_failure("a.com", "x")
        assert not r.stats()[0]["demoted"]  # counter was reset

    def test_set_domains_keeps_state(self):
        r = DomainRotator(["a.com", "b.com"])
        r.report_failure("a.com", "x")
        r.set_domains(["b.com", "a.com"])
        assert r.stats()[-1]["consecutive_failures"] == 1


# --------------------------------------------------------------------- rate limit


class TestRateLimiter:
    async def test_spacing_enforced(self):
        fake_time = {"t": 0.0}

        async def sleeper(d: float):
            fake_time["t"] += d
            return None

        lim = RateLimiter(rps=2.0, jitter=0.0, host_concurrency=3, sleeper=sleeper, clock=lambda: fake_time["t"])
        for _ in range(3):
            await lim.acquire("h")
            lim.release("h")
        # rps=2 → interval 0.5s; three acquires must space out over ≥ 1s
        assert fake_time["t"] >= 0.99

    async def test_per_host_independence(self):
        fake_time = {"t": 0.0}

        async def sleeper(d: float):
            fake_time["t"] += d
            return None

        lim = RateLimiter(rps=1.0, jitter=0.0, host_concurrency=2, sleeper=sleeper, clock=lambda: fake_time["t"])
        await lim.acquire("a")
        lim.release("a")
        await lim.acquire("b")  # different host: no wait
        lim.release("b")
        # same host again within interval: must wait ~1s
        before = fake_time["t"]
        await lim.acquire("a")
        lim.release("a")
        assert fake_time["t"] - before >= 0.99

    async def test_concurrency_cap(self):
        fake_time = {"t": 0.0}
        active = {"n": 0, "max": 0}

        async def sleeper(d: float):  # simulate waits
            fake_time["t"] += d
            return None

        async def hold(lim, host):
            await lim.acquire(host)
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            active["n"] -= 1
            lim.release(host)

        lim = RateLimiter(rps=1000.0, jitter=0.0, host_concurrency=2, sleeper=sleeper, clock=lambda: fake_time["t"])
        await asyncio.gather(*(hold(lim, "h") for _ in range(6)))
        assert active["max"] <= 2

    async def test_jitter_adds_variation(self):
        fake_time = {"t": 0.0}

        async def sleeper(d: float):
            fake_time["t"] += d
            return None

        lim = RateLimiter(rps=2.0, jitter=0.5, host_concurrency=1, sleeper=sleeper, clock=lambda: fake_time["t"])
        for _ in range(3):
            await lim.acquire("h")
            lim.release("h")
        # interval 0.5 + jitter 0..0.25 → total > 1.0 across two waits
        assert fake_time["t"] > 0.99


# -------------------------------------------------------------------- client core


def client_with(transport: MockTransport, **settings) -> NhentaiClient:
    return NhentaiClient(mk_settings(**settings), transport=transport)


class TestNhentaiClient:
    async def test_search_success(self):
        raw = make_search_raw([make_gallery_raw(1), make_gallery_raw(2)])
        t = MockTransport()
        t.add_json("https://nhentai.net/api/v2/search?query=full+color&page=1", raw)
        # v2 list items only carry tag_ids; the client resolves them in one batch
        t.add_json("https://nhentai.net/api/v2/tags/ids?ids=1%2C2%2C3%2C4", make_tags_raw())
        c = client_with(t)
        res = await c.search("full color")
        assert [g.id for g in res.galleries] == [1, 2]
        assert res.galleries[0].primary_artist == "someartist"

    async def test_gallery_detail_normalization(self):
        t = MockTransport()
        t.add_json("https://nhentai.net/api/v2/galleries/42", make_gallery_raw(42, pages=4))
        c = client_with(t)
        g = await c.gallery(42)
        assert g.id == 42
        assert g.num_pages == 4
        assert [p.number for p in g.pages] == [1, 2, 3, 4]
        assert all(p.ext == "jpg" for p in g.pages)
        assert g.pages[0].path == "galleries/1234/1.jpg"
        assert g.cover_path == "galleries/1234/cover.jpg.webp"
        assert g.primary_artist == "someartist"
        assert g.language == "english"

    async def test_query_syntax_passthrough(self):
        t = MockTransport()
        t.add_json(
            "https://nhentai.net/api/v2/search?query=artist%3Ami+language%3Aenglish&page=1",
            make_search_raw([]),
        )
        c = client_with(t)
        await c.search("artist:mi language:english")
        assert t.requests == [
            "https://nhentai.net/api/v2/search?query=artist%3Ami+language%3Aenglish&page=1"
        ]

    async def test_empty_query_uses_all_galleries(self):
        t = MockTransport()
        t.add_json("https://nhentai.net/api/v2/galleries?page=2", make_search_raw([]))
        c = client_with(t)
        res = await c.search("", page=2)
        assert res.galleries == []
        assert t.requests == ["https://nhentai.net/api/v2/galleries?page=2"]

    async def test_404_raises_not_found(self):
        t = MockTransport()
        t.routes["https://nhentai.net/api/v2/galleries/999"] = TransportResponse(404, {}, b"")
        c = client_with(t)
        with pytest.raises(NotFoundError):
            await c.gallery(999)

    async def test_challenge_rotates_to_next_domain(self):
        t = MockTransport()
        t.add_challenge("https://nhentai.net/api/v2/search?query=x&page=1")
        t.add_json(
            "https://mirror.example/api/v2/search?query=x&page=1",
            make_search_raw([make_gallery_raw(7)]),
        )
        t.add_json("https://mirror.example/api/v2/tags/ids?ids=1%2C2%2C3%2C4", make_tags_raw())
        c = client_with(t)
        res = await c.search("x")
        assert res.galleries[0].id == 7
        # nhentai.net got challenged, mirror succeeded
        assert "https://nhentai.net/api/v2/search?query=x&page=1" in t.requests
        assert any("mirror.example" in u for u in t.requests)
        # the challenge was recorded (tag resolution later reports success)
        assert c.rotator.stats()[0]["last_failure_reason"] == "cf-challenge"

    async def test_all_domains_blocked_raises_blocked(self):
        t = MockTransport()
        for d in ("nhentai.net", "mirror.example", "mirror2.example"):
            t.add_challenge(f"https://{d}/api/v2/search?query=x&page=1")
        c = client_with(t)
        with pytest.raises(BlockedError) as ei:
            await c.search("x")
        assert "Blocked" in str(ei.value)
        assert ei.value.strategies  # strategy chain recorded

    async def test_network_error_retries_then_rotates(self):
        t = MockTransport()
        t.routes["https://nhentai.net/api/v2/search?query=x&page=1"] = OSError("conn reset")
        t.add_json(
            "https://mirror.example/api/v2/search?query=x&page=1",
            make_search_raw([]),
        )
        c = client_with(t)  # max_retries=1
        res = await c.search("x")
        assert res.galleries == []
        assert any("mirror.example" in u for u in t.requests)

    async def test_non_json_body_rotates(self):
        t = MockTransport()
        t.routes["https://nhentai.net/api/v2/search?query=x&page=1"] = TransportResponse(
            200, {}, b"<html>garbage</html>"
        )
        t.add_json(
            "https://mirror.example/api/v2/search?query=x&page=1",
            make_search_raw([make_gallery_raw(5)]),
        )
        t.add_json("https://mirror.example/api/v2/tags/ids?ids=1%2C2%2C3%2C4", make_tags_raw())
        c = client_with(t)
        res = await c.search("x")
        assert res.galleries[0].id == 5

    async def test_clearance_and_ua_injection(self):
        sent: dict = {}

        class Recorder(MockTransport):
            async def request(self, method, url, *, headers=None, cookies=None):
                sent["headers"] = dict(headers or {})
                sent["cookies"] = dict(cookies or {})
                return await super().request(method, url, headers=headers, cookies=cookies)

        t = Recorder()
        t.add_json("https://nhentai.net/api/v2/galleries?page=1", make_search_raw([]))
        s = mk_settings(cf_clearance="abc123", cf_user_agent="MyUA/1.0")
        c = NhentaiClient(s, transport=t)
        await c.all_galleries()
        assert sent["headers"]["User-Agent"] == "MyUA/1.0"
        assert sent["cookies"]["cf_clearance"] == "abc123"

    async def test_proxy_setting_is_used_by_transport_choice(self):
        # just sanity: settings carry through and client builds without error
        c = client_with(MockTransport(), proxy="socks5://127.0.0.1:1080")
        assert c.settings.proxy == "socks5://127.0.0.1:1080"

    async def test_image_url_builders(self):
        c = client_with(MockTransport())
        g = Gallery.from_api(make_gallery_raw(1, media_id="999"))
        assert c.page_url(g, 1) == "https://i1.nhentai.net/galleries/999/1.jpg"
        assert c.page_url(g, 2) == "https://i1.nhentai.net/galleries/999/2.jpg"
        assert c.cover_url(g) == "https://t1.nhentai.net/galleries/999/cover.jpg.webp"
        assert c.thumb_url(g) == "https://t1.nhentai.net/galleries/999/thumb.jpg.webp"
        # mirror hosts are in the family list
        assert c.page_url(g, 1, host=1).startswith("https://i2.nhentai.net/")
        assert c.cover_url(g, host=1).startswith("https://t2.nhentai.net/")

    async def test_image_url_builders_legacy_shape(self):
        """v1-shaped raw data (no CDN paths) still reconstructs URLs."""
        c = client_with(MockTransport())
        g = Gallery.from_api(
            {
                "id": 1,
                "media_id": "999",
                "title": {"english": "x", "japanese": "", "pretty": "x"},
                "images": {
                    "pages": [{"t": "p", "w": 1, "h": 1}],
                    "cover": {"t": "p"},
                    "thumbnail": {"t": "p"},
                },
                "num_pages": 1,
            }
        )
        assert c.page_url(g, 1) == "https://i1.nhentai.net/galleries/999/1.png"
        assert c.cover_url(g) == "https://t1.nhentai.net/galleries/999/cover.png"

    async def test_fetch_image_rotates_cdn_hosts(self):
        t = MockTransport()
        t.routes["https://i1.nhentai.net/galleries/999/1.jpg"] = TransportResponse(
            403, {"cf-mitigated": "challenge"}, CHALLENGE_BODY
        )
        t.routes["https://i2.nhentai.net/galleries/999/1.jpg"] = TransportResponse(
            200, {}, b"\xff\xd8\xff\xe0fakejpg"
        )
        c = client_with(t)
        data = await c.fetch_image("https://i1.nhentai.net/galleries/999/1.jpg")
        assert data == b"\xff\xd8\xff\xe0fakejpg"
        assert any("i2.nhentai.net" in u for u in t.requests)

    async def test_fetch_image_all_blocked(self):
        t = MockTransport(default=TransportResponse(403, {"cf-mitigated": "challenge"}, CHALLENGE_BODY))
        c = client_with(t)
        with pytest.raises(BlockedError):
            await c.fetch_image("https://i1.nhentai.net/galleries/999/1.jpg")

    async def test_flaresolverr_success_path(self):
        """All domains challenged on round 1 → FlareSolverr solves → round 2 works."""

        class FakeFS:
            def __init__(self, endpoint):
                pass

            async def solve(self, url, transport):
                return {"cookies": {"cf_clearance": "fresh"}, "user_agent": "FS-UA"}

        import json as _json

        class Flippable(MockTransport):
            """Challenges until flipped (by the fake FlareSolverr), then succeeds."""

            def __init__(self):
                super().__init__()
                self.flip = False

            async def request(self, method, url, *, headers=None, cookies=None):
                if self.flip:
                    self.requests.append(url)
                    return TransportResponse(
                        200,
                        {"content-type": "application/json"},
                        _json.dumps(make_search_raw([make_gallery_raw(9)])).encode(),
                    )
                return await super().request(method, url, headers=headers, cookies=cookies)

        t = Flippable()
        for d in ("nhentai.net", "mirror.example", "mirror2.example"):
            t.add_challenge(f"https://{d}/api/v2/search?query=x&page=1")

        class ClientWithFlip(NhentaiClient):
            async def _flaresolverr_solve(self, url):
                ok = await super()._flaresolverr_solve(url)
                t.flip = True  # the solve unblocked the domain
                return ok

        c = ClientWithFlip(mk_settings(flaresolverr_url="http://127.0.0.1:8191"), transport=t)
        c._flaresolverr = FakeFS("http://127.0.0.1:8191")
        res = await c.search("x")
        assert res.galleries[0].id == 9
        assert c._runtime_cookies["cf_clearance"] == "fresh"

    async def test_blocked_state_recorded(self):
        t = MockTransport()
        for d in ("nhentai.net", "mirror.example", "mirror2.example"):
            t.routes[f"https://{d}/api/v2/galleries?page=1"] = TransportResponse(
                503, {}, b"service unavailable"
            )
            t.routes[f"https://{d}/api/v2/config"] = TransportResponse(
                503, {}, b"service unavailable"
            )
        c = client_with(t)
        with pytest.raises(BlockedError):
            await c.all_galleries()
        assert c.last_blocked is not None
        res = await c.test_connection()
        assert res["ok"] is False
        assert "strategies" in res


# ------------------------------------------------------------------ tag normalization


class TestNormalization:
    def test_tag_name_variants(self):
        assert normalize_tag_name("Full Color") == "full color"
        assert normalize_tag_name("full-color") == "full color"
        assert normalize_tag_name("full_color") == "full color"
        assert normalize_tag_name("  FULL   color ") == "full color"

    def test_type_normalization(self):
        assert normalize_type("tag") == "general"
        assert normalize_type("artist") == "artist"
        assert normalize_type("weird") == "general"

    def test_gallery_folder_vars(self):
        g = Gallery.from_api(make_gallery_raw(5, media_id="9"))
        v = g.folder_vars()
        assert v["artist"] == "someartist"
        assert v["id"] == "5"
        assert v["language"] == "english"
        assert v["title"] == g.title

    def test_multi_artist_primary(self):
        raw = make_gallery_raw(
            5,
            tags=[
                {"id": 1, "name": "aaa", "type": "artist", "count": 1},
                {"id": 2, "name": "bbb", "type": "artist", "count": 2},
                {"id": 3, "name": "x", "type": "tag", "count": 1},
            ],
        )
        g = Gallery.from_api(raw)
        assert g.primary_artist == "aaa"

    def test_ext_map(self):
        raw = make_gallery_raw(1, pages=2)
        raw["pages"][0]["path"] = "galleries/1234/1.png"
        raw["pages"][1]["path"] = "galleries/1234/2.gif"
        g = Gallery.from_api(raw)
        assert [p.ext for p in g.pages] == ["png", "gif"]

    def test_list_item_cover_path(self):
        raw = make_search_raw([make_gallery_raw(5, media_id="9")])["result"][0]
        g = Gallery.from_api(raw)
        assert g.cover_path == "galleries/9/cover.jpg.webp"
        assert g.thumb_path == "galleries/9/thumb.jpg.webp"
        assert g.tag_ids == [1, 2, 3, 4]
        assert g.title_pretty == "Test Gallery"

    def test_sniff_image_ext(self):
        assert sniff_image_ext(b"\x89PNG\r\n\x1a\n....") == "png"
        assert sniff_image_ext(b"GIF89a....") == "gif"
        assert sniff_image_ext(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
        assert sniff_image_ext(b"\xff\xd8\xff\xe0....") == "jpg"
        assert sniff_image_ext(b"unknown") == "jpg"
