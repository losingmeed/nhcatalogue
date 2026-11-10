"""Server API tests — FastAPI TestClient with a fully mocked client transport.

Covers the headline routes: search passthrough, catalogue CRUD + faceted tag
filtering, tag search/facets/groups/suggest, collections, watchlists,
downloads, reader page (disk + CDN proxy), cover, settings, progress.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import MockTransport, make_gallery_raw, make_search_raw, make_tags_raw
from fastapi.testclient import TestClient

from nhcatalogue.app import App
from nhcatalogue.client import NhentaiClient
from nhcatalogue.client.transport import TransportResponse
from nhcatalogue.server import create_app


@pytest.fixture
def server(tmp_path):
    app = App(tmp_path / "data")
    app.settings.library_root = str(tmp_path / "library")
    app.settings.rate_limit_rps = 1000
    app.settings.rate_limit_jitter = 0
    transport = MockTransport()
    # live API mocks (v2)
    transport.add_json(
        "https://nhentai.net/api/v2/search?query=mi&page=1",
        make_search_raw([make_gallery_raw(101, title="Mi Result")]),
    )
    transport.add_json(
        "https://nhentai.net/api/v2/tags/ids?ids=1%2C2%2C3%2C4", make_tags_raw()
    )
    transport.add_json(
        "https://nhentai.net/api/v2/galleries/101",
        make_gallery_raw(101, pages=4, title="Full Detail"),
    )
    transport.add_json(
        "https://nhentai.net/api/v2/galleries/202",
        make_gallery_raw(202, pages=2, title="Second"),
    )
    transport.add_json(
        "https://nhentai.net/api/v2/config",
        {"image_servers": ["https://i1.nhentai.net"], "thumb_servers": ["https://t1.nhentai.net"]},
    )
    transport.default = TransportResponse(200, {}, b"IMG-BYTES")
    client = NhentaiClient(app.settings, transport=transport)
    app._client = client

    api = create_app(app)
    with TestClient(api) as tc:
        yield tc, app, transport
    app.close()


def H(app: App) -> dict:
    return {"X-NHC-Token": app.token}


class TestGuard:
    def test_rejects_missing_token(self, server):
        tc, app, _ = server
        r = tc.get("/api/health")
        assert r.status_code == 401

    def test_rejects_bad_token(self, server):
        tc, app, _ = server
        r = tc.get("/api/health", headers={"X-NHC-Token": "wrong"})
        assert r.status_code == 401

    def test_health(self, server):
        tc, app, _ = server
        r = tc.get("/api/health", headers=H(app))
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_media_accepts_query_token(self, server):
        # <img> cannot send headers, so media routes also take ?token=
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 101, "persist": True}, headers=H(app))
        r = tc.get(f"/api/cover/101?token={app.token}")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/")

    def test_media_rejects_bad_query_token(self, server):
        tc, _, _ = server
        r = tc.get("/api/cover/101?token=wrong")
        assert r.status_code == 401

    def test_query_token_not_accepted_on_other_routes(self, server):
        tc, app, _ = server
        r = tc.get(f"/api/health?token={app.token}")
        assert r.status_code == 401


class TestSearch:
    def test_live_search_passthrough(self, server):
        tc, app, _ = server
        r = tc.get("/api/search", params={"q": "mi"}, headers=H(app))
        assert r.status_code == 200
        body = r.json()
        assert body["results"][0]["id"] == 101
        assert body["results"][0]["title_pretty"] == "Mi Result"
        assert body["results"][0]["in_catalogue"] is False

    def test_gallery_detail_live(self, server):
        tc, app, _ = server
        r = tc.get("/api/galleries/101", headers=H(app))
        assert r.status_code == 200
        assert r.json()["num_pages"] == 4
        assert len(r.json()["pages"]) == 4

    def test_search_detail_reuses_payload_then_full_on_add(self, server):
        tc, app, transport = server
        r = tc.get("/api/search", params={"q": "mi"}, headers=H(app))
        assert r.status_code == 200
        assert r.json()["results"][0]["id"] == 101

        transport.requests.clear()
        d = tc.get("/api/galleries/101", headers=H(app))
        assert d.status_code == 200
        body = d.json()
        assert body["partial"] is True
        assert body["pages"] == []
        assert transport.requests == []  # no second live detail call

        # adding to the catalogue is the point where full detail is fetched
        c = tc.post("/api/catalogue", json={"id": 101, "persist": True}, headers=H(app))
        assert c.status_code == 200
        assert len(c.json()["pages"]) == 4
        assert any("/api/v2/galleries/101" in u for u in transport.requests)


class TestCatalogue:
    def test_add_and_query(self, server):
        tc, app, _ = server
        r = tc.post("/api/catalogue", json={"id": 101, "persist": True}, headers=H(app))
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == 101
        assert body["in_catalogue"] is True
        # cover was fetched through the client and cached
        assert (app.covers_dir / "101.jpg").exists()

        r = tc.get("/api/catalogue", headers=H(app))
        assert r.json()["total"] == 1
        assert r.json()["items"][0]["id"] == 101

    def test_bare_add_is_ephemeral(self, server):
        # Big Pickle Phase 2: bare adds must not persist metadata — the comic
        # stays out of the permanent library until a collection-add/download.
        tc, app, _ = server
        r = tc.post("/api/catalogue", json={"id": 101}, headers=H(app))
        assert r.status_code == 200
        assert r.json()["in_catalogue"] is True  # UI-level marker only
        assert tc.get("/api/catalogue/101", headers=H(app)).status_code == 404
        r = tc.get("/api/catalogue", headers=H(app))
        assert r.json()["total"] == 0

    def test_persist_add_writes_catalogue(self, server):
        tc, app, _ = server
        r = tc.post("/api/catalogue", json={"id": 101, "persist": True}, headers=H(app))
        assert r.status_code == 200
        assert tc.get("/api/catalogue/101", headers=H(app)).status_code == 200
        assert tc.get("/api/catalogue", headers=H(app)).json()["total"] == 1

    def test_bare_add_marks_session_preview(self, server):
        # Big Pickle Phase 2: a preview is visible as in_catalogue for the whole
        # session (search/detail/reader), but stays out of the permanent library.
        tc, app, _ = server
        before = tc.get("/api/search", params={"q": "mi"}, headers=H(app)).json()
        assert before["results"][0]["in_catalogue"] is False
        assert before["results"][0]["persisted"] is False

        r = tc.post("/api/catalogue", json={"id": 101}, headers=H(app))
        assert r.json()["in_catalogue"] is True
        assert r.json()["persisted"] is False

        # the cached search payload is re-marked without a CDN round-trip
        after = tc.get("/api/search", params={"q": "mi"}, headers=H(app)).json()
        assert after["results"][0]["in_catalogue"] is True
        assert after["results"][0]["persisted"] is False

        detail = tc.get("/api/galleries/101", headers=H(app)).json()
        assert detail["in_catalogue"] is True
        assert detail["persisted"] is False
        assert tc.get("/api/catalogue/101", headers=H(app)).status_code == 404

        # persisting clears the preview marker and exposes the library row
        tc.post("/api/catalogue", json={"id": 101, "persist": True}, headers=H(app))
        detail = tc.get("/api/galleries/101", headers=H(app)).json()
        assert detail["in_catalogue"] is True
        assert detail["persisted"] is True

    def test_faceted_include_exclude(self, server):
        tc, app, _ = server
        for gid in (101, 202):
            tc.post("/api/catalogue", json={"id": gid, "persist": True}, headers=H(app))
        # both galleries have tag 'full color' (general) + artist someartist;
        # 101 has language english, 202 also (default fixture) — use parody-free exclusion
        r = tc.get(
            "/api/catalogue",
            params={"include": ["general:full color"], "exclude": ["language:english"]},
            headers=H(app),
        )
        assert r.json()["total"] == 0
        r = tc.get(
            "/api/catalogue",
            params={"include": ["general:full color"]},
            headers=H(app),
        )
        assert r.json()["total"] == 2

    def test_fts_search(self, server):
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 101, "persist": True}, headers=H(app))
        tc.post("/api/catalogue", json={"id": 202, "persist": True}, headers=H(app))
        r = tc.get("/api/catalogue", params={"q": "second"}, headers=H(app))
        assert r.json()["total"] == 1
        assert r.json()["items"][0]["id"] == 202
        # tag names are FTS-searchable too
        r = tc.get("/api/catalogue", params={"q": "someartist"}, headers=H(app))
        assert r.json()["total"] == 2

    def test_patch_favorite_rating_and_progress(self, server):
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 101, "persist": True}, headers=H(app))
        r = tc.patch("/api/catalogue/101", json={"favorite": True, "rating": 5}, headers=H(app))
        assert r.json()["favorite"] is True
        assert r.json()["rating"] == 5
        r = tc.get("/api/catalogue", params={"favorite": "true"}, headers=H(app))
        assert r.json()["total"] == 1

        r = tc.patch("/api/catalogue/101/progress", json={"page": 3}, headers=H(app))
        assert r.status_code == 200
        assert r.json()["read_progress_pages"] == 3
        r = tc.get("/api/catalogue/101", headers=H(app))
        assert r.json()["read_progress_pages"] == 3

    def test_delete(self, server):
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 101, "persist": True}, headers=H(app))
        r = tc.delete("/api/catalogue/101", headers=H(app))
        assert r.status_code == 200
        assert tc.get("/api/catalogue", headers=H(app)).json()["total"] == 0

    def test_delete_404(self, server):
        tc, app, _ = server
        assert tc.delete("/api/catalogue/999", headers=H(app)).status_code == 404


class TestTags:
    def test_tag_search_with_counts(self, server):
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 101, "persist": True}, headers=H(app))
        tc.post("/api/catalogue", json={"id": 202, "persist": True}, headers=H(app))
        r = tc.get("/api/tags", params={"q": "full"}, headers=H(app))
        body = r.json()
        assert body["items"]
        top = body["items"][0]
        assert top["name_norm"] == "full color"
        assert top["gallery_count"] == 2

    def test_tag_search_type_filter(self, server):
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 101, "persist": True}, headers=H(app))
        r = tc.get("/api/tags", params={"type": "artist"}, headers=H(app))
        names = [t["name"] for t in r.json()["items"]]
        assert "someartist" in names
        assert "full color" not in names

    def test_tag_suggest(self, server):
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 101, "persist": True}, headers=H(app))
        r = tc.get("/api/tags/suggest", params={"q": "some"}, headers=H(app))
        items = r.json()["items"]
        assert any(i["name"] == "someartist" and i["type"] == "artist" for i in items)

    def test_tag_groups(self, server):
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 101, "persist": True}, headers=H(app))
        groups = tc.get("/api/tags/groups", headers=H(app)).json()
        assert "artist" in groups and "language" in groups
        assert any(t["name"] == "someartist" for t in groups["artist"])

    def test_tag_facets(self, server):
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 101, "persist": True}, headers=H(app))
        facets = tc.get("/api/tags/facets", headers=H(app)).json()
        by_type = {t["type"]: t for t in facets["types"]}
        assert by_type["artist"]["gallery_count"] == 1

    def test_tag_galleries(self, server):
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 101, "persist": True}, headers=H(app))
        tag_id = tc.get("/api/tags", params={"q": "someartist"}, headers=H(app)).json()["items"][0]["id"]
        r = tc.get(f"/api/tags/{tag_id}/galleries", headers=H(app))
        assert r.json()["total"] == 1
        assert r.json()["items"][0]["id"] == 101

    def test_normalized_tag_matching_end_to_end(self, server):
        """'full color' vs 'full-color' both match the same tag."""
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 101, "persist": True}, headers=H(app))
        for spelling in ("full color", "full-color", "Full Color"):
            r = tc.get(
                "/api/catalogue",
                params={"include": [f"general:{spelling}"]},
                headers=H(app),
            )
            assert r.json()["total"] == 1, spelling


class TestCollections:
    def test_crud(self, server):
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 101}, headers=H(app))
        c = tc.post("/api/collections", json={"name": "Shelf"}, headers=H(app)).json()
        r = tc.post(f"/api/collections/{c['id']}/galleries", json={"gallery_id": 101}, headers=H(app))
        assert r.status_code == 200
        cols = tc.get("/api/collections", headers=H(app)).json()["items"]
        assert cols[0]["gallery_count"] == 1
        r = tc.get("/api/catalogue", params={"collection": c["id"]}, headers=H(app))
        assert r.json()["total"] == 1
        r = tc.delete(f"/api/collections/{c['id']}/galleries/101", headers=H(app))
        assert tc.get("/api/collections", headers=H(app)).json()["items"][0]["gallery_count"] == 0
        assert tc.delete(f"/api/collections/{c['id']}", headers=H(app)).status_code == 200


class TestWatchlists:
    def test_crud_and_sync_now(self, server):
        tc, app, transport = server
        transport.add_json(
            "https://nhentai.net/api/v2/search?query=artist%3Ami&page=1&sort=date",
            make_search_raw([make_gallery_raw(301), make_gallery_raw(302)]),
        )
        transport.add_json(
            "https://nhentai.net/api/v2/galleries/301", make_gallery_raw(301)
        )
        transport.add_json(
            "https://nhentai.net/api/v2/galleries/302", make_gallery_raw(302)
        )
        wl = tc.post(
            "/api/watchlists",
            json={"name": "watch", "query": "artist:mi"},
            headers=H(app),
        ).json()
        assert wl["id"]
        r = tc.post(f"/api/watchlists/{wl['id']}/sync-now", headers=H(app))
        assert r.status_code == 200
        assert r.json()["count"] == 2
        # galleries auto-added to catalogue
        assert tc.get("/api/catalogue", headers=H(app)).json()["total"] == 2
        # unread badge set
        wls = tc.get("/api/watchlists", headers=H(app)).json()["items"]
        assert wls[0]["unread_count"] == 2
        assert wls[0]["last_seen_id"] == 302
        # second sync: cursor prevents re-processing
        r = tc.post(f"/api/watchlists/{wl['id']}/sync-now", headers=H(app))
        assert r.json()["count"] == 0
        # mark seen clears the badge
        tc.post(f"/api/watchlists/{wl['id']}/seen", headers=H(app))
        assert tc.get("/api/watchlists", headers=H(app)).json()["items"][0]["unread_count"] == 0
        # auto-download flag update
        r = tc.patch(f"/api/watchlists/{wl['id']}", json={"auto_download": True}, headers=H(app))
        assert r.json()["auto_download"] == 1
        assert tc.delete(f"/api/watchlists/{wl['id']}", headers=H(app)).status_code == 200


class TestDownloads:
    def test_enqueue_and_progress(self, server):
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 202}, headers=H(app))
        r = tc.post("/api/downloads", json={"id": 202}, headers=H(app))
        assert r.status_code == 200
        q = r.json()
        assert q["status"] in ("queued", "running", "done")
        # let workers run
        import time

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            items = tc.get("/api/downloads", headers=H(app)).json()["items"]
            if items and items[0]["status"] == "done":
                break
            time.sleep(0.2)
        items = tc.get("/api/downloads", headers=H(app)).json()["items"]
        assert items[0]["status"] == "done"
        assert items[0]["pages_done"] == 2
        assert items[0]["folder_path"]
        # the library folder got the files
        from pathlib import Path

        folder = Path(items[0]["folder_path"])
        assert (folder / "001.jpg").exists()
        assert (folder / "metadata.json").exists()
        assert (folder / ".nhc.json").exists()

    def test_actions(self, server):
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 202}, headers=H(app))
        tc.post("/api/downloads", json={"id": 202}, headers=H(app))
        r = tc.post("/api/downloads/202", json={"action": "pause"}, headers=H(app))
        assert r.status_code == 200
        r = tc.post("/api/downloads/202", json={"action": "resume"}, headers=H(app))
        assert r.status_code == 200
        r = tc.post("/api/downloads/202", json={"action": "bogus"}, headers=H(app))
        assert r.status_code == 400


class TestReader:
    def test_page_streamed_via_client_proxy(self, server):
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 101}, headers=H(app))
        r = tc.get("/api/reader/101/page/2", headers=H(app))
        assert r.status_code == 200
        assert r.content == b"IMG-BYTES"
        assert r.headers["content-type"].startswith("image/")

    def test_page_accepts_query_token(self, server):
        # the reader loads pages through <img src="...?token=...">
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 101}, headers=H(app))
        r = tc.get(f"/api/reader/101/page/2?token={app.token}")
        assert r.status_code == 200
        assert r.content == b"IMG-BYTES"
        assert r.headers["content-type"].startswith("image/")

    def test_non_collection_page_is_not_cached(self, server):
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 101}, headers=H(app))
        r = tc.get("/api/reader/101/page/2", headers=H(app))
        assert r.status_code == 200
        assert app.page_cache.get(101, 2, "jpg", 4) is None

    def test_collection_page_cached_lazily(self, server):
        tc, app, transport = server
        tc.post("/api/catalogue", json={"id": 101}, headers=H(app))
        c = tc.post("/api/collections", json={"name": "Shelf"}, headers=H(app)).json()
        tc.post(
            f"/api/collections/{c['id']}/galleries",
            json={"gallery_id": 101},
            headers=H(app),
        )
        r = tc.get("/api/reader/101/page/2", headers=H(app))
        assert r.status_code == 200
        cached = app.page_cache.get(101, 2, "jpg", 4)
        assert cached is not None
        assert cached.read_bytes() == b"IMG-BYTES"

        # second read is served locally (no CDN request)
        transport.requests.clear()
        r2 = tc.get("/api/reader/101/page/2", headers=H(app))
        assert r2.status_code == 200
        assert r2.content == b"IMG-BYTES"
        assert transport.requests == []

        # removing it from its last collection purges the cache immediately
        tc.delete(
            f"/api/collections/{c['id']}/galleries/101", headers=H(app)
        )
        assert app.page_cache.get(101, 2, "jpg", 4) is None

    def test_deleting_collection_purges_orphaned_cache(self, server):
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 101}, headers=H(app))
        c = tc.post("/api/collections", json={"name": "Shelf"}, headers=H(app)).json()
        tc.post(
            f"/api/collections/{c['id']}/galleries",
            json={"gallery_id": 101},
            headers=H(app),
        )
        tc.get("/api/reader/101/page/2", headers=H(app))
        assert app.page_cache.get(101, 2, "jpg", 4) is not None
        tc.delete(f"/api/collections/{c['id']}", headers=H(app))
        assert app.page_cache.get(101, 2, "jpg", 4) is None

    def test_page_served_from_disk_first(self, server):
        tc, app, transport = server
        tc.post("/api/catalogue", json={"id": 202}, headers=H(app))
        tc.post("/api/downloads", json={"id": 202}, headers=H(app))
        # wait for the download to complete
        import time

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            items = tc.get("/api/downloads", headers=H(app)).json()["items"]
            if items and items[0]["status"] == "done":
                break
            time.sleep(0.2)
        items = tc.get("/api/downloads", headers=H(app)).json()["items"]
        assert items[0]["status"] == "done"
        folder = Path(items[0]["folder_path"])
        assert (folder / "002.jpg").exists()
        transport.requests.clear()
        r = tc.get("/api/reader/202/page/2", headers=H(app))
        assert r.status_code == 200
        assert r.content == b"IMG-BYTES"  # the bytes the downloader wrote to disk
        # no network hit for that page
        assert not any("/2.jpg" in u for u in transport.requests)
        # downloaded galleries use the library folder — no duplicate page cache
        assert app.page_cache.get(202, 2, "jpg", 2) is None

    def test_reader_info(self, server):
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 101}, headers=H(app))
        r = tc.get("/api/reader/101/info", headers=H(app))
        assert r.status_code == 200
        assert r.json()["num_pages"] == 4
        assert r.json()["pages"][0]["n"] == 1

    def test_cover_cached(self, server):
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 101, "persist": True}, headers=H(app))
        r = tc.get("/api/cover/101", headers=H(app))
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/")

    def test_bare_add_reads_via_temp_cache_then_expires(self, server):
        # Big Pickle Phase 2: bare (ephemeral) adds read through the temporary
        # reader cache; entries expire after the 10s retention window.
        import os as _os
        import time as _time

        tc, app, transport = server
        tc.post("/api/catalogue", json={"id": 101}, headers=H(app))
        r = tc.get("/api/reader/101/page/2", headers=H(app))
        assert r.status_code == 200
        assert r.content == b"IMG-BYTES"
        temp = app.temp_page_cache.get(101, 2, "jpg", 4)
        assert temp is not None
        assert app.page_cache.get(101, 2, "jpg", 4) is None

        # within retention: served from the temp cache, no CDN round-trip
        transport.requests.clear()
        assert tc.get("/api/reader/101/page/2", headers=H(app)).content == b"IMG-BYTES"
        assert transport.requests == []

        # retention elapsed → temp entry dropped → CDN serves the page again
        _os.utime(temp, (_time.time() - 100, _time.time() - 100))
        assert app.temp_page_cache.get(101, 2, "jpg", 4) is None
        transport.requests.clear()
        assert tc.get("/api/reader/101/page/2", headers=H(app)).content == b"IMG-BYTES"
        assert len(transport.requests) == 1

    def test_collection_add_promotes_temp_cache_to_permanent(self, server):
        # Big Pickle Phase 2: adding a comic to a collection during the temp
        # retention window promotes metadata + cached pages to permanent storage.
        tc, app, transport = server
        tc.post("/api/catalogue", json={"id": 101}, headers=H(app))  # bare → temp
        r = tc.get("/api/reader/101/page/2", headers=H(app))
        assert r.status_code == 200
        assert app.temp_page_cache.get(101, 2, "jpg", 4) is not None

        c = tc.post("/api/collections", json={"name": "Shelf"}, headers=H(app)).json()
        tc.post(
            f"/api/collections/{c['id']}/galleries",
            json={"gallery_id": 101},
            headers=H(app),
        )

        # promotion: metadata persisted + temp entries moved to permanent cache
        prow = tc.get("/api/catalogue/101", headers=H(app))
        assert prow.status_code == 200
        assert any(t["name"] == "full color" for t in prow.json()["tags"])
        assert app.temp_page_cache.get(101, 2, "jpg", 4) is None
        assert app.page_cache.get(101, 2, "jpg", 4) is not None

        # post-promotion reads are served from the permanent cache (no CDN hit)
        transport.requests.clear()
        r2 = tc.get("/api/reader/101/page/2", headers=H(app))
        assert r2.content == b"IMG-BYTES"
        assert transport.requests == []

    def test_persist_add_promotes_temp_cache(self, server):
        # Big Pickle Phase 2: explicitly saving a previewed comic promotes its
        # retained pages to permanent storage as well.
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 101}, headers=H(app))
        assert tc.get("/api/reader/101/page/2", headers=H(app)).status_code == 200
        assert app.temp_page_cache.get(101, 2, "jpg", 4) is not None

        r = tc.post("/api/catalogue", json={"id": 101, "persist": True}, headers=H(app))
        assert r.status_code == 200
        assert r.json()["persisted"] is True
        assert app.temp_page_cache.get(101, 2, "jpg", 4) is None
        assert app.page_cache.get(101, 2, "jpg", 4) is not None

    def test_download_enqueue_promotes_temp_cache(self, server):
        # Big Pickle Phase 2: queueing a download also makes the gallery permanent
        # immediately, so retained preview pages move into the permanent cache.
        tc, app, _ = server
        tc.post("/api/catalogue", json={"id": 101}, headers=H(app))
        assert tc.get("/api/reader/101/page/2", headers=H(app)).status_code == 200
        assert app.temp_page_cache.get(101, 2, "jpg", 4) is not None

        r = tc.post("/api/downloads", json={"id": 101}, headers=H(app))
        assert r.status_code == 200
        assert tc.get("/api/catalogue/101", headers=H(app)).status_code == 200
        assert app.temp_page_cache.get(101, 2, "jpg", 4) is None
        assert app.page_cache.get(101, 2, "jpg", 4) is not None


class TestSettings:
    def test_get_settings(self, server):
        tc, app, _ = server
        r = tc.get("/api/settings", headers=H(app))
        body = r.json()
        assert body["domains"][0] == "nhentai.net"
        assert "token" in body
        assert body["has_curl_cffi"] in (True, False)

    def test_put_settings_updates_client(self, server):
        tc, app, _ = server
        r = tc.put(
            "/api/settings",
            json={"patch": {"domains": ["mirror.example", "nhentai.net"], "rate_limit_rps": 2}},
            headers=H(app),
        )
        assert r.status_code == 200
        assert app.settings.domains == ["mirror.example", "nhentai.net"]
        assert app.client.rotator.order()[0] == "mirror.example"
        assert app.client.limiter.interval == 0.5

    def test_fast_preset_applies_live(self, server):
        tc, app, _ = server
        r = tc.put(
            "/api/settings",
            json={
                "patch": {
                    "rate_limit_rps": 4,
                    "rate_limit_jitter": 0.15,
                    "host_concurrency": 6,
                    "download_page_concurrency": 6,
                    "download_workers": 4,
                    "page_cache_max_mb": 512,
                }
            },
            headers=H(app),
        )
        assert r.status_code == 200
        assert app.settings.download_page_concurrency == 6
        assert app.downloader.workers == 4
        assert app.page_cache.max_bytes == 512 * 1024 * 1024

    def test_temp_cache_ttl_applies_live(self, server):
        tc, app, _ = server
        r = tc.put(
            "/api/settings",
            json={"patch": {"temp_page_cache_ttl_s": 42, "rate_limit_rps": 4}},
            headers=H(app),
        )
        assert r.status_code == 200
        assert app.settings.temp_page_cache_ttl_s == 42
        assert app.temp_page_cache.ttl_seconds == 42
        # the cache cap is shared by the permanent and preview caches
        tc.put(
            "/api/settings",
            json={"patch": {"page_cache_max_mb": 128}},
            headers=H(app),
        )
        assert app.temp_page_cache.max_bytes == 128 * 1024 * 1024

    def test_put_settings_validation(self, server):
        tc, app, _ = server
        r = tc.put("/api/settings", json={"patch": {"rate_limit_rps": 99}}, headers=H(app))
        assert r.status_code == 422
        r = tc.put("/api/settings", json={"patch": {"folder_template": "{nope}"}}, headers=H(app))
        assert r.status_code == 422

    def test_settings_test_connection(self, server):
        tc, app, transport = server
        transport.add_json(
            "https://nhentai.net/api/v2/config",
            {"image_servers": ["https://i1.nhentai.net"], "thumb_servers": ["https://t1.nhentai.net"]},
        )
        r = tc.post("/api/settings/test", headers=H(app))
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_settings_test_blocked(self, server):
        tc, app, transport = server
        from conftest import CHALLENGE_BODY

        # drop the healthy config mock so the challenge default applies
        transport.routes.pop("https://nhentai.net/api/v2/config", None)
        transport.default = TransportResponse(403, {"cf-mitigated": "challenge"}, CHALLENGE_BODY)
        r = tc.post("/api/settings/test", headers=H(app))
        body = r.json()
        assert body["ok"] is False
        assert "strategies" in body


class TestImport:
    def test_import_endpoint(self, server):
        tc, app, _ = server
        from conftest import make_gallery_raw as mgr

        from nhcatalogue.client.models import Gallery
        from nhcatalogue.downloader import manifests as mf

        lib = app.library_root
        folder = lib / "someartist" / "303 - Imported"
        folder.mkdir(parents=True)
        (folder / "001.jpg").write_bytes(b"x")
        mf.write_metadata(folder, Gallery.from_api(mgr(303, pages=1, title="Imported")))
        r = tc.post("/api/import", params={"path": str(lib)}, headers=H(app))
        assert r.status_code == 200
        assert r.json()["imported"] == 1
        assert tc.get("/api/galleries/303", headers=H(app)).json()["download_status"] == "done"


class TestLiveCache:
    def test_search_cached(self, server):
        tc, app, transport = server
        r1 = tc.get("/api/search", params={"q": "mi"}, headers=H(app))
        n_requests = len(transport.requests)
        r2 = tc.get("/api/search", params={"q": "mi"}, headers=H(app))
        assert len(transport.requests) == n_requests  # served from cache
        assert r1.json() == r2.json()
