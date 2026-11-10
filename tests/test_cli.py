"""CLI end-to-end tests with a fully mocked transport (no network)."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import pytest
from conftest import MockTransport, make_gallery_raw, make_search_raw

import nhcatalogue.client.api as api_mod
from nhcatalogue.__main__ import main
from nhcatalogue.client.transport import TransportResponse


@pytest.fixture
def mocked_client(monkeypatch):
    """Patch build_transport so the CLI's client uses MockTransport."""
    transport = MockTransport(default=TransportResponse(200, {}, b"IMGDATA"))

    def fake_build(settings):
        return transport

    monkeypatch.setattr(api_mod, "build_transport", fake_build)
    return transport


def run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


class TestCli:
    def test_search(self, tmp_path, mocked_client):
        mocked_client.add_json(
            "https://nhentai.net/api/galleries/search?query=mi&page=1&sort=",
            make_search_raw([make_gallery_raw(11, title="Found It")]),
        )
        rc, out = run_cli(["--data-dir", str(tmp_path), "search", "mi"])
        assert rc == 0
        assert "#11" in out and "Found It" in out

    def test_add(self, tmp_path, mocked_client):
        mocked_client.add_json(
            "https://nhentai.net/api/galleries/42", make_gallery_raw(42, pages=5, title="Added")
        )
        rc, out = run_cli(["--data-dir", str(tmp_path), "add", "42"])
        assert rc == 0
        assert "added to catalogue #42" in out
        # persisted: DB row + cover cache
        db_path = tmp_path / "catalogue.sqlite3"
        assert db_path.exists()
        covers = list((tmp_path / "covers").glob("42.*"))
        assert covers, "cover should be cached"

    def test_download(self, tmp_path, mocked_client):
        mocked_client.add_json(
            "https://nhentai.net/api/galleries/77", make_gallery_raw(77, pages=3, title="DL Me")
        )
        rc, out = run_cli(["--data-dir", str(tmp_path), "download", "77"])
        assert rc == 0
        # folder structure per the template: <artist>/<id> - <title>/
        folder = tmp_path / "library" / "someartist" / "77 - DL Me [Digital]"
        assert (folder / "001.jpg").exists()
        assert (folder / "002.jpg").exists()
        assert (folder / "003.jpg").exists()
        assert (folder / "cover.jpg").exists()
        assert (folder / "metadata.json").exists()
        assert (folder / ".nhc.json").exists()
        meta = json.loads((folder / "metadata.json").read_text())
        assert meta["id"] == 77
        assert "✓" in out or "done" in out

    def test_import(self, tmp_path, mocked_client):
        # build a fake existing library
        lib = tmp_path / "inlib" / "someartist" / "123 - Existing Gallery"
        lib.mkdir(parents=True)
        (lib / "001.jpg").write_bytes(b"a")
        (lib / "002.jpg").write_bytes(b"b")
        from nhcatalogue.client.models import Gallery
        from nhcatalogue.downloader import manifests as mf

        mf.write_metadata(lib, Gallery.from_api(make_gallery_raw(123, pages=2, title="Existing Gallery")))
        rc, out = run_cli(["--data-dir", str(tmp_path), "import", str(tmp_path / "inlib")])
        assert rc == 0
        assert "imported 1 galleries" in out
        assert "123" in out

    def test_status(self, tmp_path, mocked_client):
        rc, out = run_cli(["--data-dir", str(tmp_path), "status"])
        assert rc == 0
        assert "nhcatalogue 0.1.0" in out
        assert "domains" in out
        assert "library root" in out
        assert str(tmp_path / "library") in out

    def test_version(self, tmp_path):
        with pytest.raises(SystemExit) as e:
            main(["--version"])
        assert e.value.code == 0
