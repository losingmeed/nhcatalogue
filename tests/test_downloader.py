"""Downloader tests: resume from manifest, folder templating, sanitization, import."""

from __future__ import annotations

import asyncio
from pathlib import Path

from conftest import make_gallery_raw

from nhcatalogue.client.models import Gallery
from nhcatalogue.downloader import (
    build_manifest,
    import_folder,
    import_library,
    page_filename,
    render_template,
    sanitize_component,
)
from nhcatalogue.downloader import manifests as mf


def make_gallery(gid: int = 1, *, pages: int = 4, title: str = "My Title", tags=None) -> Gallery:
    return Gallery.from_api(make_gallery_raw(gid, pages=pages, title=title, tags=tags))


class TestPaths:
    def test_sanitize_illegal_chars(self):
        assert sanitize_component('a<b>c:"d"/e\\f|g?h*i') == "a b c d e f g h i"
        assert sanitize_component("con") == "_con"
        assert sanitize_component("...") == "unknown"
        assert sanitize_component("") == "unknown"
        assert sanitize_component("trailing... ") == "trailing"
        assert sanitize_component("a" * 500) == "a" * 120

    def test_template_render_default(self):
        g = make_gallery()
        vars = g.folder_vars()
        out = render_template("{artist}/{id} - {title}", vars)
        assert out == f"someartist/1 - {g.title}"

    def test_template_render_all_vars(self):
        g = make_gallery(tags=[
            {"id": 1, "name": "artistX", "type": "artist", "count": 1},
            {"id": 2, "name": "parodyY", "type": "parody", "count": 1},
            {"id": 3, "name": "charZ", "type": "character", "count": 1},
            {"id": 4, "name": "english", "type": "language", "count": 1},
        ])
        out = render_template(
            "{parody}/{character}/{language}/{artist}/{id} {title}", g.folder_vars()
        )
        assert out.startswith("parodyY/charZ/english/artistX/1 ")

    def test_template_missing_vars_unknown(self):
        assert render_template("{artist}/{id}", {"id": "5"}) == "unknown/5"
        assert render_template("{unknownvar}/{id}", {"id": "5"}) == "unknown/5"
        assert render_template("plain text", {}) == "plain text"

    def test_template_traversal_blocked(self):
        out = render_template("{artist}/../../etc", {"artist": "x"})
        assert ".." not in out
        assert out.startswith("x/")

    def test_template_custom(self):
        out = render_template(
            "{language}/{artist}/{id} - {title}",
            {"language": "english", "artist": "some artist", "id": "42", "title": "T: A/B?"},
        )
        assert out == "english/some artist/42 - T A B"

    def test_page_filenames_zero_padded(self):
        assert page_filename(1, "jpg", 120) == "001.jpg"
        assert page_filename(99, "png", 120) == "099.png"
        assert page_filename(100, "jpg", 120) == "100.jpg"
        assert page_filename(1, "gif", 5) == "001.gif"  # min 3 digits

    def test_manifest_and_metadata_compatibility(self):
        g = make_gallery(pages=3)
        m = build_manifest(g)
        assert m["gallery_id"] == g.id
        assert len(m["expected"]) == 3
        meta = mf.build_metadata(g)
        # Houdoku-compatible shape
        assert meta["id"] == g.id
        assert set(meta["title"]) == {"english", "japanese", "pretty"}
        assert set(meta["images"]) == {"pages", "cover", "thumbnail"}
        assert meta["num_pages"] == 3


class TestManifestResume:
    def test_mark_and_verify(self, tmp_path):
        g = make_gallery(pages=3)
        m = build_manifest(g)
        mf.write_manifest(tmp_path, m)

        # page 1 "downloaded" (file exists, size matches)
        (tmp_path / "001.jpg").write_bytes(b"x" * 10)
        mf.mark_page_downloaded(m, 1, 10)
        mf.write_manifest(tmp_path, m)

        m2 = mf.read_manifest(tmp_path)
        assert m2["gallery_id"] == g.id
        assert mf.verify_page(m2, 1, tmp_path, "jpg") is True
        # page 2 never downloaded
        assert mf.verify_page(m2, 2, tmp_path, "jpg") is False
        # page 1 with wrong size fails integrity
        (tmp_path / "001.jpg").write_bytes(b"x" * 99)
        assert mf.verify_page(m2, 1, tmp_path, "jpg") is False
        # zero-size file fails
        (tmp_path / "001.jpg").write_bytes(b"")
        assert mf.verify_page(m2, 1, tmp_path, "jpg") is False

    def test_resume_skips_downloaded_pages(self, tmp_path):
        """End-to-end: a partially-downloaded folder only fetches missing pages."""
        from conftest import MockTransport

        from nhcatalogue.app import App
        from nhcatalogue.client.transport import TransportResponse

        g = make_gallery(77, pages=3, title="Resume Me")
        app = App(tmp_path / "data")
        app.settings.library_root = str(tmp_path / "library")
        app.settings.rate_limit_rps = 1000
        app.settings.rate_limit_jitter = 0
        try:
            asyncio.run(app.db.upsert_gallery(g))

            folder = app.downloader.gallery_folder(g)
            # pretend page 1 was already downloaded
            folder.mkdir(parents=True)
            (folder / "001.jpg").write_bytes(b"page-one-bytes")
            m = build_manifest(g)
            mf.mark_page_downloaded(m, 1, 14)
            mf.write_manifest(folder, m)

            fetched: list[str] = []

            class Rec(MockTransport):
                async def request(self, method, url, *, headers=None, cookies=None):
                    fetched.append(url)
                    return TransportResponse(200, {}, b"IMGDATA" + url.encode()[-5:])

            from nhcatalogue.client import NhentaiClient

            app._client = NhentaiClient(app.settings, transport=Rec())

            async def run():
                await app.downloader.enqueue(77)
                await asyncio.sleep(0.5)

            asyncio.run(run())
            # verify only pages 2,3 (+cover) were fetched — page 1 resumed from disk
            page_fetches = [u for u in fetched if any(f"/{n}.jpg" in u for n in (1, 2, 3))]
            assert any("/2.jpg" in u for u in page_fetches)
            assert any("/3.jpg" in u for u in page_fetches)
            assert not any("/1.jpg" in u for u in page_fetches), "page 1 should be resumed from disk"
            # files exist with padded names
            assert (folder / "002.jpg").exists()
            assert (folder / "003.jpg").exists()
            # manifest complete + metadata written
            m2 = mf.read_manifest(folder)
            assert m2["complete"] is True
            meta = mf.read_metadata(folder)
            assert meta["id"] == 77
            # DB updated
            row = asyncio.run(app.db.get_gallery(77))
            assert row["download_status"] == "done"
            assert row["folder_path"] == str(folder)
        finally:
            app.close()

    def test_parallel_pages_rotate_cdn_hosts(self, tmp_path):
        from conftest import MockTransport

        from nhcatalogue.app import App
        from nhcatalogue.client.transport import TransportResponse

        g = make_gallery(99, pages=8, title="Parallel")
        app = App(tmp_path / "data")
        app.settings.library_root = str(tmp_path / "library")
        app.settings.rate_limit_rps = 1000
        app.settings.rate_limit_jitter = 0
        app.settings.download_page_concurrency = 4
        try:
            asyncio.run(app.db.upsert_gallery(g))

            hosts: list[str] = []
            active = {"n": 0, "max": 0}

            class Slow(MockTransport):
                async def request(self, method, url, *, headers=None, cookies=None):
                    if url.endswith(".jpg"):
                        hosts.append(url.split("/")[2])
                        active["n"] += 1
                        active["max"] = max(active["max"], active["n"])
                        await asyncio.sleep(0.02)
                        active["n"] -= 1
                    return TransportResponse(200, {}, b"PAGEBYTES")

            from nhcatalogue.client import NhentaiClient

            app._client = NhentaiClient(app.settings, transport=Slow())

            async def run():
                await app.downloader.enqueue(99)
                await asyncio.sleep(2.0)

            asyncio.run(run())
            assert active["max"] > 1, "pages should be fetched in parallel"
            assert len(set(hosts)) >= 2, f"pages should rotate CDN hosts: {hosts}"
            folder = app.downloader.gallery_folder(g)
            for n in range(1, 9):
                assert (folder / f"{n:03d}.jpg").exists()
        finally:
            app.close()

    def test_download_writes_all_files(self, tmp_path):
        from conftest import MockTransport

        from nhcatalogue.app import App
        from nhcatalogue.client.transport import TransportResponse

        g = make_gallery(88, pages=2, title="Fresh Download")
        app = App(tmp_path / "data")
        app.settings.library_root = str(tmp_path / "library")
        app.settings.rate_limit_rps = 1000
        app.settings.rate_limit_jitter = 0
        try:
            asyncio.run(app.db.upsert_gallery(g))
            t = MockTransport(default=TransportResponse(200, {}, b"IMGDATA1"))
            from nhcatalogue.client import NhentaiClient

            app._client = NhentaiClient(app.settings, transport=t)

            async def run():
                await app.downloader.enqueue(88)
                await asyncio.sleep(0.6)

            asyncio.run(run())
            folder = app.downloader.gallery_folder(g)
            assert (folder / "001.jpg").exists()
            assert (folder / "002.jpg").exists()
            assert (folder / "cover.webp").exists()
            assert (folder / "metadata.json").exists()
            assert (folder / ".nhc.json").exists()
            row = asyncio.run(app.db.get_gallery(88))
            assert row["download_status"] == "done"
        finally:
            app.close()


class TestImport:
    def make_library(self, root: Path) -> tuple[Path, Path]:
        g = make_gallery(123, pages=2, title="Imported Gallery")
        f1 = root / "someartist" / "123 - Imported Gallery"
        f1.mkdir(parents=True)
        (f1 / "001.jpg").write_bytes(b"a")
        (f1 / "002.jpg").write_bytes(b"b")
        mf.write_metadata(f1, g)

        # numeric folder without metadata
        f2 = root / "456 - No Meta"
        f2.mkdir(parents=True)
        (f2 / "001.png").write_bytes(b"a")
        (f2 / "002.png").write_bytes(b"b")
        return f1, f2

    def test_import_folder_with_metadata(self, tmp_path):
        import asyncio

        from nhcatalogue.db import Database

        db = Database(tmp_path / "t.sqlite3")
        g = make_gallery(123, pages=2, title="Imported Gallery")
        f1 = tmp_path / "lib" / "artist" / "123 - Imported Gallery"
        f1.mkdir(parents=True)
        (f1 / "001.jpg").write_bytes(b"a")
        (f1 / "002.jpg").write_bytes(b"b")
        mf.write_metadata(f1, g)

        gid = asyncio.run(import_folder(db, f1))
        assert gid == 123
        row = asyncio.run(db.get_gallery(123))
        assert row["download_status"] == "done"
        assert row["folder_path"] == str(f1)
        assert row["num_pages"] == 2
        db.close()

    def test_import_folder_numeric_name(self, tmp_path):
        import asyncio

        from nhcatalogue.db import Database

        db = Database(tmp_path / "t.sqlite3")
        f2 = tmp_path / "lib" / "456 - No Meta"
        f2.mkdir(parents=True)
        (f2 / "001.png").write_bytes(b"a")
        (f2 / "002.png").write_bytes(b"b")

        gid = asyncio.run(import_folder(db, f2))
        assert gid == 456
        row = asyncio.run(db.get_gallery(456))
        assert row["num_pages"] == 2  # counted from files
        assert row["download_status"] == "done"
        db.close()

    def test_import_library_scan(self, tmp_path):
        import asyncio

        from nhcatalogue.db import Database

        db = Database(tmp_path / "t.sqlite3")
        f1, f2 = self.make_library(tmp_path / "lib")
        ids = asyncio.run(import_library(db, tmp_path / "lib"))
        assert set(ids) == {123, 456}
        row = asyncio.run(db.get_gallery(123))
        assert row["folder_path"] == str(f1)
        db.close()

    def test_import_ignores_unrecognized(self, tmp_path):
        import asyncio

        from nhcatalogue.db import Database

        db = Database(tmp_path / "t.sqlite3")
        junk = tmp_path / "lib" / "not a gallery"
        junk.mkdir(parents=True)
        ids = asyncio.run(import_library(db, tmp_path / "lib"))
        assert ids == []
        db.close()

    def test_reimport_does_not_duplicate(self, tmp_path):
        import asyncio

        from nhcatalogue.db import Database

        db = Database(tmp_path / "t.sqlite3")
        self.make_library(tmp_path / "lib")
        asyncio.run(import_library(db, tmp_path / "lib"))
        ids2 = asyncio.run(import_library(db, tmp_path / "lib"))
        assert len(ids2) == 2  # same two galleries, no dupes
        assert asyncio.run(db.count_galleries()) == 2
        db.close()


class TestDownloaderControl:
    def test_dedup_by_gallery_id_folder(self, tmp_path):
        from nhcatalogue.app import App

        app = App(tmp_path / "data")
        app.settings.library_root = str(tmp_path / "library")
        try:
            g = make_gallery(55, title="Dedup")
            folder = app.downloader.gallery_folder(g)
            folder.mkdir(parents=True)
            found = app.downloader.existing_folder_for(55)
            assert found == folder
            assert app.downloader.existing_folder_for(9999) is None
        finally:
            app.close()
