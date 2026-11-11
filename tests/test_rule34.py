"""Rule 34 source tests: migration safety, client normalisation, API routes.

No real network calls — everything goes through ``MockTransport``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from conftest import MockTransport
from fastapi.testclient import TestClient

from nhcatalogue.app import App
from nhcatalogue.client import NhentaiClient
from nhcatalogue.client.models import Gallery
from nhcatalogue.client.rule34 import RULE34_ID_OFFSET, decode_id, encode_id
from nhcatalogue.client.sources import SOURCES, get_source
from nhcatalogue.client.transport import TransportResponse
from nhcatalogue.server import create_app

MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "nhcatalogue" / "db" / "migrations"


def make_post_raw(
    post_id: int = 555,
    *,
    ext: str = ".jpg",
    size: int = 123456,
    url: str | None = None,
    tags: dict | str | None = None,
) -> dict:
    """The real dapi post shape: flat fields, values as strings."""
    if tags is None:
        tags = "artist:someartist cat solo"
    return {
        "id": post_id,
        "tags": tags,
        "file_url": url or f"https://rule34.xxx/image/{post_id}/file{ext}",
        "created_at": "Wed Oct 22 09:14:33 +0000 2025",
        "rating": "s",
        "type": "0",
        "author": "1",
        "status": "active",
        "parent_id": "0",
        "has_children": "no",
        "rating_score": "0",
        "creator_id": "42",
        "creator_name": "someone",
        "sample_url": f"https://rule34.xxx/samples/{post_id}/sample.jpg",
        "preview_url": f"https://rule34.xxx/preview/{post_id}/prev.jpg",
        "width": "1280",
        "height": "1808",
        "source": "some source",
        "file_size": str(size),
        "file_hash": "abcdef",
    }


API = "https://api.rule34.xxx"


def api_url(path: str, **params) -> str:
    """Rebuild the exact URL the client builds (params + auth, in order)."""
    from urllib.parse import urlencode

    query = {k: v for k, v in params.items() if v is not None and v != ""}
    query["api_key"] = "secret"
    query["user_id"] = "1234"
    url = f"{API}{path}"
    return url + ("&" if "?" in url else "?") + urlencode(query)


def search_url(query: str = "", page: int = 1) -> str:
    return api_url(
        "/index.php?page=dapi&s=post&q=index",
        json=1,
        limit=40,
        pid=page,
        fields="tag_info",
        tags=query or None,
    )


def post_url(post_id: int) -> str:
    return api_url("/index.php?page=dapi&s=post&q=index", json=1, id=post_id)


def H(app: App) -> dict:
    return {"X-NHC-Token": app.token}


@pytest.fixture
def rule34_server(tmp_path):
    app = App(tmp_path / "data")
    app.settings.library_root = str(tmp_path / "library")
    app.settings.rate_limit_rps = 1000
    app.settings.rate_limit_jitter = 0
    app.settings.rule34_user_id = "1234"
    app.settings.rule34_api_key = "secret"
    transport = MockTransport()
    transport.add_json(search_url(), [make_post_raw(555), make_post_raw(556, ext=".mp4", size=50 * 1024 * 1024)])
    transport.add_json(post_url(555), make_post_raw(555))
    transport.add_json(post_url(556), make_post_raw(556, ext=".mp4", size=50 * 1024 * 1024))
    transport.add_json(
        "https://nhentai.net/api/v2/galleries?page=1",
        {"result": [], "num_pages": 1, "per_page": 25, "total": 0},
    )
    transport.add_json(
        api_url("/index.php?page=dapi&s=tag&q=index", json=1, limit=50),
        [{"id": 1, "name": "cat", "type": "general", "count": 900}],
    )
    transport.add_json(
        api_url("/autocomplete.php", q="ca"), ["cat", "cathedral"]
    )
    transport.default = TransportResponse(200, {}, b"IMG-BYTES")
    app._client = NhentaiClient(app.settings, transport=transport)

    api = create_app(app)
    with TestClient(api) as tc:
        yield tc, app, transport
    app.close()


class TestMigration006:
    """The tags/galleries rebuild must not lose a single row."""

    def _db_with_data(self, tmp_path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(tmp_path / "t.sqlite3")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        for f in sorted(MIGRATIONS.glob("00[1-5]*.sql")):
            conn.executescript(f.read_text())
        conn.execute(
            "INSERT INTO galleries(id, media_id, title_en, title_pretty, num_pages, raw_json)"
            " VALUES (1,'abc','T1','T1',5,'{}')"
        )
        conn.execute(
            "INSERT INTO tags(id,name,name_norm,type,count) VALUES (10,'Cat','cat','general',9)"
        )
        conn.execute(
            "INSERT INTO tags(id,name,name_norm,type,count) VALUES (11,'ArtistX','artistx','artist',4)"
        )
        conn.execute("INSERT INTO gallery_tags(gallery_id, tag_id) VALUES (1,10)")
        conn.execute("INSERT INTO gallery_tags(gallery_id, tag_id) VALUES (1,11)")
        conn.execute("INSERT INTO collections(name) VALUES ('Shelf')")
        conn.execute("INSERT INTO collection_galleries(collection_id, gallery_id) VALUES (1,1)")
        conn.execute("INSERT INTO download_queue(gallery_id) VALUES (1)")
        conn.commit()
        return conn

    def test_migration_preserves_all_rows(self, tmp_path):
        conn = self._db_with_data(tmp_path)
        tables = (
            "galleries",
            "tags",
            "gallery_tags",
            "collections",
            "collection_galleries",
            "download_queue",
        )
        before = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}
        conn.executescript((MIGRATIONS / "006_source_tags.sql").read_text())
        conn.commit()
        after = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}
        assert before == after
        assert after["gallery_tags"] == 2

    def test_migration_keeps_existing_tags_as_nhentai(self, tmp_path):
        conn = self._db_with_data(tmp_path)
        conn.executescript((MIGRATIONS / "006_source_tags.sql").read_text())
        conn.commit()
        rows = [dict(r) for r in conn.execute("SELECT name, source FROM tags")]
        assert {r["source"] for r in rows} == {"nhentai"}
        gallery = dict(conn.execute("SELECT id, source FROM galleries").fetchone())
        assert gallery["source"] == "nhentai"

    def test_migration_allows_same_tag_name_per_source(self, tmp_path):
        conn = self._db_with_data(tmp_path)
        conn.executescript((MIGRATIONS / "006_source_tags.sql").read_text())
        conn.execute(
            "INSERT INTO tags(id,name,name_norm,type,count,source)"
            " VALUES (12,'Cat','cat','general',1,'rule34')"
        )
        conn.commit()
        rows = [dict(r) for r in conn.execute("SELECT name, source FROM tags ORDER BY id")]
        assert ("Cat", "nhentai") in [(r["name"], r["source"]) for r in rows]
        assert ("Cat", "rule34") in [(r["name"], r["source"]) for r in rows]

    def test_migration_leaves_no_foreign_key_violations(self, tmp_path):
        conn = self._db_with_data(tmp_path)
        conn.executescript((MIGRATIONS / "006_source_tags.sql").read_text())
        conn.commit()
        assert list(conn.execute("PRAGMA foreign_key_check")) == []

    def test_migration_keeps_fts_triggers_working(self, tmp_path):
        conn = self._db_with_data(tmp_path)
        conn.executescript((MIGRATIONS / "006_source_tags.sql").read_text())
        conn.execute(
            "INSERT INTO tags(id,name,name_norm,type,count,source)"
            " VALUES (12,'New','new','general',1,'rule34')"
        )
        conn.execute("INSERT INTO gallery_tags(gallery_id, tag_id) VALUES (1,12)")
        conn.commit()
        row = conn.execute("SELECT tags FROM galleries_fts WHERE rowid=1").fetchone()
        assert "New" in row["tags"]


class TestRule34Client:
    def test_id_namespace_roundtrip(self):
        assert encode_id(555) == 555 + RULE34_ID_OFFSET
        assert decode_id(encode_id(555)) == 555

    def test_post_normalised_to_gallery(self):
        from nhcatalogue.client.rule34 import Rule34Client
        from nhcatalogue.config import Settings

        client = Rule34Client(Settings(), MockTransport())
        g = client._post_to_gallery(make_post_raw(555))
        assert isinstance(g, Gallery)
        assert g.id == encode_id(555)
        assert g.media_id == "555"
        assert g.source == "rule34"
        assert g.media_kind == "image"
        assert g.file_size == 123456
        assert g.num_pages == 1
        # images are served from the wimg host
        assert g.pages[0].path == "https://wimg.xxx/image/555/file.jpg"
        assert g.pages[0].ext == "jpg"
        assert g.pages[0].width == 1280
        assert g.pages[0].height == 1808
        names = {t.name for t in g.tags}
        assert names == {"someartist", "cat", "solo"}
        types = {t.name: t.type for t in g.tags}
        assert types["someartist"] == "artist"
        assert types["cat"] == "general"
        # the rule34 date format is parsed
        assert g.upload_date > 0

    def test_video_detected(self):
        from nhcatalogue.client.rule34 import Rule34Client
        from nhcatalogue.config import Settings

        client = Rule34Client(Settings(), MockTransport())
        g = client._post_to_gallery(make_post_raw(556, ext=".mp4", size=50 * 1024 * 1024))
        assert g.media_kind == "video"
        assert g.pages[0].ext == "mp4"
        assert g.file_size == 50 * 1024 * 1024

    def test_cdn_url_rewrites_image_host(self):
        from nhcatalogue.client.rule34 import cdn_url

        assert cdn_url("https://rule34.xxx/image/1/a.jpg") == "https://wimg.xxx/image/1/a.jpg"
        # already rewritten / api hosts are left alone
        assert cdn_url("https://wimg.xxx/image/1/a.jpg") == "https://wimg.xxx/image/1/a.jpg"
        assert cdn_url("https://api.rule34.xxx/x") == "https://api.rule34.xxx/x"

    def test_auth_travels_as_query_params(self):
        from nhcatalogue.client.rule34 import Rule34Client
        from nhcatalogue.config import Settings

        s = Settings()
        s.rule34_user_id = "7040482"
        s.rule34_api_key = "deadbeef"
        client = Rule34Client(s, MockTransport())
        assert client._auth_params() == {"api_key": "deadbeef", "user_id": "7040482"}
        assert client.api_host == "api.rule34.xxx"
        assert client.domain == "rule34.xxx"

    def test_plain_string_tags_parsed(self):
        from nhcatalogue.client.rule34 import Rule34Client
        from nhcatalogue.config import Settings

        client = Rule34Client(Settings(), MockTransport())
        g = client._post_to_gallery(make_post_raw(555, tags="cat solo"))
        assert {t.name for t in g.tags} == {"cat", "solo"}
        assert all(t.type == "general" for t in g.tags)

    def test_source_registry(self):
        assert set(SOURCES) == {"nhentai", "rule34"}
        assert get_source("rule34").is_comic is False
        assert get_source("rule34").has_video is True
        assert get_source("nhentai").is_comic is True
        assert get_source("nonsense").name == "nhentai"


class TestRule34Routes:
    def test_sources_endpoint_lists_both(self, rule34_server):
        tc, app, _ = rule34_server
        r = tc.get("/api/sources", headers=H(app))
        assert r.status_code == 200
        names = [s["name"] for s in r.json()["sources"]]
        assert names == ["nhentai", "rule34"]

    def test_status_reports_credentials(self, rule34_server):
        tc, app, _ = rule34_server
        r = tc.get("/api/rule34/status", headers=H(app))
        assert r.status_code == 200
        body = r.json()
        assert body["configured"] is True
        assert body["needs_credentials"] is False

    def test_status_flags_missing_credentials(self, tmp_path):
        app = App(tmp_path / "data")
        app.settings.rate_limit_rps = 1000
        app._client = NhentaiClient(app.settings, transport=MockTransport())
        with TestClient(create_app(app)) as tc:
            body = tc.get("/api/rule34/status", headers=H(app)).json()
        assert body["configured"] is False
        assert body["needs_credentials"] is True
        app.close()

    def test_search_returns_rule34_results(self, rule34_server):
        tc, app, _ = rule34_server
        r = tc.get("/api/search", params={"q": "", "source": "rule34"}, headers=H(app))
        assert r.status_code == 200
        results = r.json()["results"]
        assert len(results) == 2
        assert results[0]["source"] == "rule34"
        assert results[0]["id"] == encode_id(555)
        assert results[1]["media_kind"] == "video"

    def test_search_uses_source_specific_tags(self, rule34_server):
        tc, app, transport = rule34_server
        tc.get("/api/search", params={"q": "cat", "source": "rule34"}, headers=H(app))
        assert any("tags=cat" in u for u in transport.requests)

    def test_nhentai_search_unaffected(self, rule34_server):
        tc, app, _ = rule34_server
        r = tc.get("/api/search", params={"q": ""}, headers=H(app))
        assert r.status_code == 200
        for item in r.json()["results"]:
            assert item["source"] == "nhentai"

    def test_post_detail_reports_size_and_kind(self, rule34_server):
        tc, app, _ = rule34_server
        r = tc.get(f"/api/rule34/post/{encode_id(555)}", headers=H(app))
        assert r.status_code == 200
        body = r.json()
        assert body["post_id"] == 555
        assert body["kind"] == "image"
        assert body["file_size"] == 123456
        assert body["large_file"] is False
        assert body["large_file_threshold"] == 10 * 1024 * 1024

    def test_large_file_flagged_over_threshold(self, rule34_server):
        tc, app, _ = rule34_server
        body = tc.get(f"/api/rule34/post/{encode_id(556)}", headers=H(app)).json()
        assert body["kind"] == "video"
        assert body["large_file"] is True

    def test_media_served(self, rule34_server):
        tc, app, transport = rule34_server
        r = tc.get(
            f"/api/rule34/media/{encode_id(555)}",
            params={"token": app.token},
        )
        assert r.status_code == 200
        assert r.content == b"IMG-BYTES"

    def test_media_requires_token(self, rule34_server):
        tc, app, _ = rule34_server
        r = tc.get(f"/api/rule34/media/{encode_id(555)}")
        assert r.status_code == 401

    def test_rule34_tag_search_uses_rule34_endpoint(self, rule34_server):
        tc, app, transport = rule34_server
        r = tc.get(
            "/api/tags/search", params={"q": "", "source": "rule34"}, headers=H(app)
        )
        assert r.status_code == 200
        body = r.json()
        assert body["provider"] == "rule34"
        assert body["source"] == "live"
        assert any("s=tag" in u for u in transport.requests)

    def test_rule34_autocomplete_endpoint(self, rule34_server):
        tc, app, transport = rule34_server
        r = tc.get(
            "/api/tags/search", params={"q": "ca", "source": "rule34"}, headers=H(app)
        )
        assert r.status_code == 200
        names = [t["name"] for t in r.json()["items"]]
        assert "cat" in names
        assert any("autocomplete.php" in u for u in transport.requests)


class TestRule34Catalogue:
    def test_saving_rule34_post_keeps_sources_apart(self, rule34_server):
        tc, app, _ = rule34_server
        gid = encode_id(555)
        r = tc.post("/api/catalogue", json={"id": gid, "persist": True}, headers=H(app))
        assert r.status_code == 200
        row = app.db.conn.execute(
            "SELECT id, source FROM galleries WHERE id=?", (gid,)
        ).fetchone()
        assert row is not None
        assert row["source"] == "rule34"

    def test_rule34_and_nhentai_ids_do_not_collide(self, rule34_server):
        tc, app, _ = rule34_server
        gid = encode_id(555)
        tc.post("/api/catalogue", json={"id": gid, "persist": True}, headers=H(app))
        assert app.db.conn.execute(
            "SELECT count(*) FROM galleries WHERE id=?", (gid,)
        ).fetchone()[0] == 1
        # a plain nhentai id of 555 must be a different row
        assert app.db.conn.execute(
            "SELECT count(*) FROM galleries WHERE id=?", (555,)
        ).fetchone()[0] == 0


class TestRule34Download:
    def test_rule34_post_downloads_to_library(self, rule34_server):
        import time

        tc, app, transport = rule34_server
        gid = encode_id(555)
        r = tc.post("/api/downloads", json={"id": gid, "persist": True}, headers=H(app))
        assert r.status_code == 200

        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            items = tc.get("/api/downloads", headers=H(app)).json()["items"]
            if items and items[0]["status"] in ("done", "error"):
                break
            time.sleep(0.2)
        items = tc.get("/api/downloads", headers=H(app)).json()["items"]
        assert items[0]["status"] == "done", items[0].get("error")

        folder = Path(items[0]["folder_path"])
        assert folder.exists()
        # the media file landed on disk with the right extension
        assert (folder / "001.jpg").exists()
        assert (folder / "metadata.json").exists()

    def test_rule34_download_uses_rule34_media_url(self, rule34_server):
        import time

        tc, app, transport = rule34_server
        gid = encode_id(555)
        tc.post("/api/downloads", json={"id": gid, "persist": True}, headers=H(app))
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            items = tc.get("/api/downloads", headers=H(app)).json()["items"]
            if items and items[0]["status"] in ("done", "error"):
                break
            time.sleep(0.2)
        assert any("wimg.xxx/image/555" in u for u in transport.requests)

    def test_video_download_uses_mp4_extension(self, rule34_server):
        import time

        tc, app, _ = rule34_server
        gid = encode_id(556)
        tc.post("/api/downloads", json={"id": gid, "persist": True}, headers=H(app))
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            items = tc.get("/api/downloads", headers=H(app)).json()["items"]
            if items and items[0]["status"] in ("done", "error"):
                break
            time.sleep(0.2)
        items = tc.get("/api/downloads", headers=H(app)).json()["items"]
        assert items[0]["status"] == "done", items[0].get("error")
        folder = Path(items[0]["folder_path"])
        assert (folder / "001.mp4").exists()
