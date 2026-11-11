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
    ext: str = ".jpeg",
    url: str | None = None,
    tags: str | None = None,
    tag_info: list | None = None,
) -> dict:
    """The real dapi JSON post shape (verified against the live API).

    Flat fields, numeric values as numbers, and the media on the
    ``api-cdn.rule34.xxx`` host.
    """
    if tags is None:
        tags = "1girl solo smile"
    if tag_info is None:
        tag_info = [
            {"count": 3303757, "type": "tag", "tag": "1girl"},
            {"count": 12, "type": "artist", "tag": "someartist"},
            {"count": 5, "type": "character", "tag": "somechar"},
        ]
    return {
        "id": post_id,
        "tags": tags,
        "tag_info": tag_info,
        "file_url": url
        or f"https://api-cdn.rule34.xxx/images/3538/{post_id:08x}{ext}",
        "preview_url": f"https://api-cdn.rule34.xxx/thumbnails/3538/thumb_{post_id:08x}.jpg",
        "sample_url": f"https://api-cdn.rule34.xxx/samples/3538/sample_{post_id:08x}.jpg",
        "directory": 3538,
        "image": f"{post_id:08x}.jpeg",
        "hash": f"{post_id:08x}",
        "width": 1280,
        "height": 1808,
        "sample": True,
        "sample_width": 850,
        "sample_height": 922,
        "change": 1794402370,
        "created_at": None,
        "source": "https://example.invalid/art",
        "owner": "somebody",
        "creator_id": "42",
        "rating": "explicit",
        "status": "active",
        "parent_id": 0,
        "score": 2,
        "comment_count": 0,
        "has_notes": False,
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


def ac_url(q: str) -> str:
    """Autocomplete URL — the empty query is meaningful and is kept."""
    from urllib.parse import urlencode

    query = {"q": q, "api_key": "secret", "user_id": "1234"}
    return f"{API}/autocomplete.php?" + urlencode(query)


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
    return api_url(
        "/index.php?page=dapi&s=post&q=index", json=1, id=post_id, fields="tag_info"
    )


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
    transport.add_json(search_url(), [make_post_raw(555), make_post_raw(556, ext=".mp4")])
    transport.add_json(post_url(555), make_post_raw(555))
    transport.add_json(post_url(556), make_post_raw(556, ext=".mp4"))
    transport.add_json(
        "https://nhentai.net/api/v2/galleries?page=1",
        {"result": [], "num_pages": 1, "per_page": 25, "total": 0},
    )
    # the autocomplete endpoint answers [{"label": "name (count)", "value": "name"}]
    transport.add_json(
        ac_url(""),
        [
            {"label": "female (10623105)", "value": "female"},
            {"label": "breasts (8714477)", "value": "breasts"},
        ],
    )
    transport.add_json(
        ac_url("ca"),
        [
            {"label": "cat (900)", "value": "cat"},
            {"label": "cathedral (870)", "value": "cathedral"},
        ],
    )
    # the tag endpoint answers an exact-name lookup with the namespace code
    transport.add_text(
        api_url("/index.php?page=dapi&s=tag&q=index", name="cat"),
        '<tags type="array"><tag type="0" count="27829" name="cat" id="4514"/></tags>',
    )
    transport.add_text(
        api_url("/index.php?page=dapi&s=tag&q=index", name="female"),
        '<tags type="array"><tag type="0" count="10623105" name="female" id="99"/></tags>',
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
        assert g.num_pages == 1
        # the media url is used verbatim: the api already points at the
        # right cdn host, and rewriting it to wimg.* would 403
        assert g.pages[0].path == "https://api-cdn.rule34.xxx/images/3538/0000022b.jpeg"
        assert g.pages[0].ext == "jpeg"
        assert g.pages[0].width == 1280
        assert g.pages[0].height == 1808
        # the cover is the small thumbnail
        assert g.cover_path.endswith("thumb_0000022b.jpg")        # tag_info supplies the namespace and the post count
        types = {t.name: t.type for t in g.tags}
        assert types["someartist"] == "artist"
        assert types["somechar"] == "character"
        assert types["1girl"] == "general"
        counts = {t.name: t.count for t in g.tags}
        assert counts["1girl"] == 3303757
        # the rule34 date format is parsed (from "change")
        assert g.upload_date == 1794402370
        assert g.scanlator == "somebody"

    def test_video_detected(self):
        from nhcatalogue.client.rule34 import Rule34Client
        from nhcatalogue.config import Settings

        client = Rule34Client(Settings(), MockTransport())
        g = client._post_to_gallery(
            make_post_raw(556, url="https://api-cdn-mp4.rule34.xxx/images/3538/a.mp4")
        )
        assert g.media_kind == "video"
        assert g.pages[0].ext == "mp4"

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
        g = client._post_to_gallery(
            make_post_raw(555, tags="cat solo", tag_info=[])
        )
        assert {t.name for t in g.tags} == {"cat", "solo"}
        assert all(t.type == "general" for t in g.tags)

    def test_tag_info_names_use_tag_key(self):
        from nhcatalogue.client.rule34 import _parse_tags

        tags = _parse_tags([{"count": 5, "type": "artist", "tag": "someartist"}])
        assert [t.name for t in tags] == ["someartist"]
        assert tags[0].type == "artist"
        assert tags[0].count == 5

    def test_tag_xml_namespace_codes(self):
        from nhcatalogue.client.rule34 import _parse_xml_tags

        xml = (
            '<tags type="array">'
            '<tag type="1" count="900" name="cat" id="1"/>'
            '<tag type="0" count="500" name="solo" id="2"/>'
            '<tag type="4" count="7" name="somechar" id="3"/>'
            '<tag type="3" count="8" name="somecopyright" id="4"/>'
            '<tag type="5" count="9" name="ai_generated" id="5"/>'
            "</tags>"
        )
        tags = {t.name: t for t in _parse_xml_tags(xml)}
        assert tags["cat"].type == "artist"
        assert tags["solo"].type == "general"
        assert tags["somechar"].type == "character"
        assert tags["somecopyright"].type == "copyright"
        assert tags["ai_generated"].type == "metadata"
        assert tags["cat"].count == 900

    def test_tag_info_metadata_becomes_other(self):
        """rule34's "metadata" namespace must not fold into "general"."""
        from nhcatalogue.client.rule34 import Rule34Client
        from nhcatalogue.config import Settings

        client = Rule34Client(Settings(), MockTransport())
        g = client._post_to_gallery(
            make_post_raw(
                555,
                tag_info=[
                    {"count": 10, "type": "tag", "tag": "1girl"},
                    {"count": 20, "type": "metadata", "tag": "ai_generated"},
                    {"count": 30, "type": "artist", "tag": "someartist"},
                ],
            )
        )
        types = {t.name: t.type for t in g.tags}
        assert types["1girl"] == "general"
        assert types["ai_generated"] == "other"
        assert types["someartist"] == "artist"

    def test_tag_query_drops_namespace_prefixes(self):
        """The dapi matches plain tag names; a prefix makes it unmatchable."""
        from nhcatalogue.client.rule34 import normalize_tag_query

        assert normalize_tag_query("artist:foo") == "foo"
        assert normalize_tag_query("general:1girl") == "1girl"
        assert normalize_tag_query("-artist:foo") == "-foo"
        assert normalize_tag_query("1girl solo") == "1girl solo"
        assert normalize_tag_query('"long tag"') == "long tag"
        # an unknown prefix is left alone (it may be part of the tag name)
        assert normalize_tag_query("weird:thing") == "weird:thing"
        assert normalize_tag_query("") == ""

    def test_search_sends_plain_tag_names(self, rule34_server):
        tc, app, transport = rule34_server
        tc.get(
            "/api/search",
            params={"q": "artist:cat solo", "source": "rule34"},
            headers=H(app),
        )
        sent = [u for u in transport.requests if "tags=" in u]
        assert sent, "no tag query was sent"
        assert "tags=cat+solo" in sent[0]
        assert "artist%3A" not in sent[0]

    def test_source_registry(self):
        assert set(SOURCES) == {"nhentai", "rule34"}
        assert get_source("rule34").is_comic is False
        assert get_source("rule34").has_video is True
        assert get_source("nhentai").is_comic is True
        assert get_source("nonsense").name == "nhentai"


class TestRule34EmptyResults:
    """The dapi answers a query that matches nothing with an empty body.

    That is a valid empty result set, not an error — treating it as a
    failure turned every no-match search into a bogus "rate limited" error.
    """

    def test_empty_body_is_an_empty_result_set(self, tmp_path):
        import asyncio

        from nhcatalogue.client.rule34 import Rule34Client
        from nhcatalogue.config import Settings

        transport = MockTransport()
        transport.add_empty(search_url("zzzznotarealtag"))
        s = Settings()
        s.rule34_user_id = "1234"
        s.rule34_api_key = "secret"
        s.rule34_rate_limit_rps = 1000
        client = Rule34Client(s, transport)

        result = asyncio.run(client.search("zzzznotarealtag"))
        assert result.galleries == []

    def test_empty_body_for_a_missing_post_is_not_found(self, tmp_path):
        import asyncio

        from nhcatalogue.client.errors import NotFoundError
        from nhcatalogue.client.rule34 import Rule34Client
        from nhcatalogue.config import Settings

        transport = MockTransport()
        transport.add_empty(post_url(99999999999))
        s = Settings()
        s.rule34_user_id = "1234"
        s.rule34_api_key = "secret"
        s.rule34_rate_limit_rps = 1000
        client = Rule34Client(s, transport)

        with pytest.raises(NotFoundError):
            asyncio.run(client.post(99999999999))


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
        # the dapi does not report a file size, so the warning stays off
        assert body["file_size"] == 0
        assert body["large_file"] is False
        assert body["large_file_threshold"] == 10 * 1024 * 1024

    def test_video_kind_reported(self, rule34_server):
        tc, app, _ = rule34_server
        body = tc.get(f"/api/rule34/post/{encode_id(556)}", headers=H(app)).json()
        assert body["kind"] == "video"

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
        # the popular tags come from the autocomplete endpoint
        assert any("autocomplete.php" in u for u in transport.requests)
        names = {t["name"] for t in body["items"]}
        assert "female" in names
        counts = {t["name"]: t["count"] for t in body["items"]}
        assert counts["female"] == 10623105

    def test_rule34_tag_types_are_enriched(self, rule34_server):
        """The picker shows namespaces; autocomplete only reports names."""
        tc, app, transport = rule34_server
        body = tc.get(
            "/api/tags/search", params={"q": "ca", "source": "rule34"}, headers=H(app)
        ).json()
        items = {t["name"]: t for t in body["items"]}
        assert items["cat"]["type"] == "general"
        # the exact lookup supplies the id and the real post count
        assert items["cat"]["id"] == 4514
        assert items["cat"]["count"] == 27829
        assert any("name=cat" in u for u in transport.requests)

    def test_rule34_autocomplete_endpoint(self, rule34_server):
        tc, app, transport = rule34_server
        r = tc.get(
            "/api/tags/search", params={"q": "ca", "source": "rule34"}, headers=H(app)
        )
        assert r.status_code == 200
        items = r.json()["items"]
        names = [t["name"] for t in items]
        assert "cat" in names
        assert "cathedral" in names
        # the count comes from the exact lookup (the authoritative one)
        counts = {t["name"]: t["count"] for t in items}
        assert counts["cat"] == 27829
        assert counts["cathedral"] == 870
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

    def test_saving_rule34_post_files_its_tags_under_rule34(self, rule34_server):
        """A rule34 gallery's tags must not be filed as nhentai tags.

        The Tag dataclass defaults to source="nhentai", so the gallery's
        source has to win — otherwise a rule34 tag with the same name as an
        nhentai one would share its row and the two sources' counts would
        be merged.
        """
        tc, app, _ = rule34_server
        gid = encode_id(555)
        tc.post("/api/catalogue", json={"id": gid, "persist": True}, headers=H(app))
        rows = app.db.conn.execute(
            "SELECT name, source FROM tags WHERE name IN ('1girl', 'someartist', 'somechar')"
        ).fetchall()
        assert rows
        assert {r["source"] for r in rows} == {"rule34"}
        # the join actually points at the rule34 rows
        joined = app.db.conn.execute(
            "SELECT t.source FROM gallery_tags gt JOIN tags t ON t.id = gt.tag_id "
            "WHERE gt.gallery_id=?",
            (gid,),
        ).fetchall()
        assert joined
        assert {r["source"] for r in joined} == {"rule34"}

    def test_same_tag_name_can_exist_for_both_sources(self, rule34_server):
        import asyncio

        tc, app, _ = rule34_server
        gid = encode_id(555)
        tc.post("/api/catalogue", json={"id": gid, "persist": True}, headers=H(app))
        # an nhentai gallery with an identically named tag
        from nhcatalogue.client.models import Gallery, Tag

        g = Gallery(
            id=1,
            media_id="m1",
            title_en="t",
            title_ja="",
            title_pretty="t",
            source="nhentai",
            tags=[Tag(id=0, name="1girl", type="general", count=1)],
        )
        asyncio.run(app.db.upsert_gallery(g))
        rows = app.db.conn.execute(
            "SELECT source, count(*) c FROM tags WHERE name='1girl' GROUP BY source"
        ).fetchall()
        by_source = {r["source"]: r["c"] for r in rows}
        assert by_source.get("rule34") == 1
        assert by_source.get("nhentai") == 1

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
        assert any("api-cdn.rule34.xxx/images/3538" in u for u in transport.requests)

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
