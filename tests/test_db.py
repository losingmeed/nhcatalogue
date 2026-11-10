"""DB tests: schema, migrations, FTS, tag search, catalogue queries."""

from __future__ import annotations

from conftest import make_gallery_raw

from nhcatalogue.client.models import Gallery, Tag
from nhcatalogue.db import Database


def gallery(gid: int, *, title: str = "Test", tags=None, pages: int = 3) -> Gallery:
    return Gallery.from_api(make_gallery_raw(gid, title=title, tags=tags, pages=pages))


def tagset(*names: str) -> list[dict]:
    out = []
    for i, n in enumerate(names):
        ty = "artist" if n.startswith("artist:") else ("language" if n.startswith("lang:") else "tag")
        name = n.split(":", 1)[1] if ":" in n else n
        out.append({"id": 100 + i, "name": name, "type": ty, "count": i})
    return out


class TestSchema:
    def test_tables_exist(self, tmp_path):
        db = Database(tmp_path / "t.sqlite3")
        names = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for expected in (
            "galleries", "tags", "gallery_tags", "collections", "collection_galleries",
            "watchlists", "download_queue", "settings", "galleries_fts",
        ):
            assert expected in names, f"missing table {expected}"
        db.close()

    def test_migrations_are_sequential_and_idempotent(self, tmp_path):
        db = Database(tmp_path / "t.sqlite3")
        rows = db.conn.execute("SELECT name FROM schema_migrations ORDER BY name").fetchall()
        assert [r[0] for r in rows] == sorted(r[0] for r in rows)
        # reopen: no duplicate migrations applied
        db.close()
        db2 = Database(tmp_path / "t.sqlite3")
        rows2 = db2.conn.execute("SELECT count(*) FROM schema_migrations").fetchone()
        assert rows2[0] == len(rows)
        db2.close()

    def test_fts_triggers_keep_in_sync(self, tmp_path):
        db = Database(tmp_path / "t.sqlite3")
        g = gallery(1, title="Alpha Delta")
        g.tags = [Tag(id=9, name="big boobs", type="general"), Tag(id=10, name="x artist", type="artist")]
        import asyncio

        asyncio.run(db.upsert_gallery(g))
        # title token searchable
        n = db.conn.execute(
            "SELECT count(*) FROM galleries_fts WHERE galleries_fts MATCH 'delta'"
        ).fetchone()[0]
        assert n == 1
        # tag name searchable (FTS over titles + tag names)
        n = db.conn.execute(
            "SELECT count(*) FROM galleries_fts WHERE galleries_fts MATCH 'boobs'"
        ).fetchone()[0]
        assert n == 1
        # delete removes from FTS
        asyncio.run(db.delete_gallery(1))
        n = db.conn.execute(
            "SELECT count(*) FROM galleries_fts WHERE galleries_fts MATCH 'delta'"
        ).fetchone()[0]
        assert n == 0
        db.close()


class TestUpserts:
    def test_upsert_and_read(self, tmp_path):
        import asyncio

        db = Database(tmp_path / "t.sqlite3")
        g = gallery(1)
        asyncio.run(db.upsert_gallery(g))
        row = asyncio.run(db.get_gallery(1))
        assert row["id"] == 1
        assert row["title_pretty"] == g.title_pretty
        assert {t["name"] for t in row["tags"]} == {"full color", "someartist", "english", "manga"}

    def test_upsert_preserves_user_data(self, tmp_path):
        import asyncio

        db = Database(tmp_path / "t.sqlite3")
        asyncio.run(db.upsert_gallery(gallery(1, title="Old")))
        asyncio.run(db.update_user_data(1, {"favorite": True, "rating": 4}))
        asyncio.run(db.upsert_gallery(gallery(1, title="New")))
        row = asyncio.run(db.get_gallery(1))
        assert row["title_pretty"] == "New [Digital]"
        assert row["favorite"] is True
        assert row["rating"] == 4

    def test_tag_normalization_matches_across_spellings(self, tmp_path):
        import asyncio

        db = Database(tmp_path / "t.sqlite3")
        # "full color" from one gallery, "full-color" spelling from another:
        g1 = gallery(1, tags=[{"id": 50, "name": "full color", "type": "tag", "count": 5}])
        g2 = gallery(2, tags=[{"id": 50, "name": "full-color", "type": "tag", "count": 5}])
        asyncio.run(db.upsert_gallery(g1))
        asyncio.run(db.upsert_gallery(g2))
        # one normalized tag row only
        rows = db.conn.execute("SELECT name_norm FROM tags WHERE name_norm='full color'").fetchall()
        assert len(rows) == 1
        # tag search finds it by fragment
        res = asyncio.run(db.tag_search(q="full co"))
        assert res["items"][0]["name_norm"] == "full color"
        assert res["items"][0]["gallery_count"] == 2


class TestTagSearch:
    def test_tag_search_with_counts_and_order(self, tmp_path):
        import asyncio

        db = Database(tmp_path / "t.sqlite3")
        common = {"id": 50, "name": "full color", "type": "tag", "count": 500}
        rare = {"id": 51, "name": "rare tag", "type": "tag", "count": 3}
        artist = {"id": 52, "name": "artistA", "type": "artist", "count": 40}
        for gid in (1, 2, 3):
            asyncio.run(db.upsert_gallery(gallery(gid, tags=[common, rare])))
        asyncio.run(db.upsert_gallery(gallery(4, tags=[common, artist])))

        res = asyncio.run(db.tag_search())
        assert res["total"] >= 3
        names = [t["name"] for t in res["items"]]
        assert "full color" in names and "artistA" in names
        # ordered by gallery count desc → full color first
        assert res["items"][0]["name"] == "full color"
        assert res["items"][0]["gallery_count"] == 4

    def test_tag_search_filter_by_type(self, tmp_path):
        import asyncio

        db = Database(tmp_path / "t.sqlite3")
        asyncio.run(
            db.upsert_gallery(
                gallery(1, tags=[
                    {"id": 1, "name": "aa", "type": "artist", "count": 1},
                    {"id": 2, "name": "bb", "type": "tag", "count": 1},
                ])
            )
        )
        res = asyncio.run(db.tag_search(type_="artist"))
        assert [t["name"] for t in res["items"]] == ["aa"]

    def test_tags_by_group(self, tmp_path):
        import asyncio

        db = Database(tmp_path / "t.sqlite3")
        asyncio.run(
            db.upsert_gallery(
                gallery(1, tags=[
                    {"id": 1, "name": "aa", "type": "artist", "count": 1},
                    {"id": 2, "name": "bb", "type": "tag", "count": 1},
                    {"id": 3, "name": "english", "type": "language", "count": 9},
                ])
            )
        )
        groups = asyncio.run(db.tags_by_group())
        assert set(groups) >= {"artist", "general", "language"}
        assert groups["artist"][0]["name"] == "aa"

    def test_tag_galleries_query(self, tmp_path):
        import asyncio

        db = Database(tmp_path / "t.sqlite3")
        tag = {"id": 50, "name": "sole unique tag", "type": "tag", "count": 5}
        for gid in (1, 2):
            asyncio.run(db.upsert_gallery(gallery(gid, tags=[tag])))
        asyncio.run(db.upsert_gallery(gallery(3, tags=[{"id": 99, "name": "other", "type": "tag", "count": 1}])))
        res = asyncio.run(db.query_catalogue(tag_id=50))
        assert res["total"] == 2
        assert {g["id"] for g in res["items"]} == {1, 2}


class TestCatalogueQueries:
    def test_include_exclude_tag_filters(self, tmp_path):
        import asyncio

        db = Database(tmp_path / "t.sqlite3")
        yaoi = {"id": 60, "name": "yaoi", "type": "tag", "count": 1}
        english = {"id": 61, "name": "english", "type": "language", "count": 1}
        asyncio.run(db.upsert_gallery(gallery(1, tags=[yaoi, english])))
        asyncio.run(db.upsert_gallery(gallery(2, tags=[english])))
        asyncio.run(db.upsert_gallery(gallery(3, tags=[yaoi])))

        inc = asyncio.run(db.query_catalogue(include_tags=[{"type": "language", "name": "english"}]))
        assert {g["id"] for g in inc["items"]} == {1, 2}
        exc = asyncio.run(
            db.query_catalogue(
                include_tags=[{"type": "language", "name": "english"}],
                exclude_tags=[{"type": "general", "name": "yaoi"}],
            )
        )
        assert {g["id"] for g in exc["items"]} == {2}
        # normalized matching: "yaoi" with different spelling still matches
        exc2 = asyncio.run(
            db.query_catalogue(exclude_tags=[{"type": "general", "name": "YaoI"}])
        )
        assert {g["id"] for g in exc2["items"]} == {2}

    def test_fts_search_over_titles_and_tags(self, tmp_path):
        import asyncio

        db = Database(tmp_path / "t.sqlite3")
        asyncio.run(db.upsert_gallery(gallery(1, title="Vanilla Romance")))
        g2 = gallery(2, title="Something Else")
        g2.tags = [Tag(id=70, name="vanilla", type="general")]
        asyncio.run(db.upsert_gallery(g2))
        # FTS should find title matches...
        res = asyncio.run(db.query_catalogue(q="vanilla"))
        assert res["total"] == 2  # title match + tag match
        res = asyncio.run(db.query_catalogue(q="romance"))
        assert res["total"] == 1

    def test_favorite_rating_downloaded_filters(self, tmp_path):
        import asyncio

        db = Database(tmp_path / "t.sqlite3")
        for gid in (1, 2, 3):
            asyncio.run(db.upsert_gallery(gallery(gid)))
        asyncio.run(db.update_user_data(1, {"favorite": True, "rating": 5}))
        asyncio.run(db.update_user_data(2, {"favorite": True, "rating": 2}))
        asyncio.run(db.update_user_data(3, {"download_status": "done"}))

        res = asyncio.run(db.query_catalogue(favorite=True))
        assert {g["id"] for g in res["items"]} == {1, 2}
        res = asyncio.run(db.query_catalogue(favorite=True, rating_min=4))
        assert {g["id"] for g in res["items"]} == {1}
        res = asyncio.run(db.query_catalogue(downloaded=True))
        assert {g["id"] for g in res["items"]} == {3}

    def test_facets_respect_current_filters(self, tmp_path):
        import asyncio

        db = Database(tmp_path / "t.sqlite3")
        yaoi = {"id": 60, "name": "yaoi", "type": "tag", "count": 1}
        english = {"id": 61, "name": "english", "type": "language", "count": 1}
        asyncio.run(db.upsert_gallery(gallery(1, tags=[yaoi, english])))
        asyncio.run(db.upsert_gallery(gallery(2, tags=[yaoi])))
        facets = asyncio.run(
            db.tag_facets(include_tags=[{"type": "language", "name": "english"}])
        )
        by_type = {t["type"]: t for t in facets["types"]}
        assert by_type["general"]["gallery_count"] == 1  # only gallery 1 remains

    def test_progress_update(self, tmp_path):
        import asyncio

        db = Database(tmp_path / "t.sqlite3")
        asyncio.run(db.upsert_gallery(gallery(1)))
        row = asyncio.run(db.set_progress(1, 12))
        assert row["read_progress_pages"] == 12
        assert row["last_read_at"]

    def test_collections(self, tmp_path):
        import asyncio

        db = Database(tmp_path / "t.sqlite3")
        asyncio.run(db.upsert_gallery(gallery(1)))
        c = asyncio.run(db.collection_create("Favorites", "desc"))
        asyncio.run(db.collection_add(c["id"], 1))
        cols = asyncio.run(db.collections())
        assert cols[0]["name"] == "Favorites"
        assert cols[0]["gallery_count"] == 1
        res = asyncio.run(db.query_catalogue(collection=c["id"]))
        assert res["total"] == 1

    def test_watchlist_and_queue(self, tmp_path):
        import asyncio

        db = Database(tmp_path / "t.sqlite3")
        asyncio.run(db.upsert_gallery(gallery(1, pages=5)))
        wl = asyncio.run(db.watchlist_create("w", "artist:x", True))
        assert wl["auto_download"] == 1
        asyncio.run(db.watchlist_update(wl["id"], {"unread_count": 3}))
        qid = asyncio.run(db.queue_add(1, 5))
        assert qid
        # dedup: same gallery not queued twice while active
        assert asyncio.run(db.queue_add(1, 5)) == qid
        asyncio.run(db.queue_update(qid, {"status": "done", "progress": 100.0}))
        items = asyncio.run(db.queue_list())
        assert items[0]["status"] == "done"
        # after done, a new queue row can be added
        assert asyncio.run(db.queue_add(1, 5)) != qid

    def test_settings_kv(self, tmp_path):
        import asyncio

        db = Database(tmp_path / "t.sqlite3")
        asyncio.run(db.settings_set("k", {"a": 1}))
        all_ = asyncio.run(db.settings_get_all())
        assert all_["k"] == {"a": 1}
