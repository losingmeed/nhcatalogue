"""App context tests: one-time settings upgrade from the old polite defaults."""

from __future__ import annotations

from nhcatalogue.app import App
from nhcatalogue.config import Settings


def _write_old_settings(app: App, **overrides) -> None:
    old = Settings().to_dict()
    old.update(
        {
            "rate_limit_rps": 1.0,
            "rate_limit_jitter": 0.35,
            "host_concurrency": 3,
            "download_workers": 3,
        }
    )
    # these fields did not exist before the speed/cache change
    old.pop("download_page_concurrency", None)
    old.pop("page_cache_max_mb", None)
    old.update(overrides)
    app.db.settings_set_sync("settings", old)
    app.close()


def test_old_defaults_upgrade_to_fast(tmp_path):
    data = tmp_path / "data"
    _write_old_settings(App(data))

    app = App(data)
    try:
        assert app.settings.rate_limit_rps == 4.0
        assert app.settings.rate_limit_jitter == 0.15
        assert app.settings.host_concurrency == 6
        assert app.settings.download_workers == 4
        assert app.settings.download_page_concurrency == 6
        assert app.settings.page_cache_max_mb == 2048
    finally:
        app.close()

    # the upgrade is persisted, so a second boot keeps the Fast values
    again = App(data)
    try:
        assert again.settings.rate_limit_rps == 4.0
    finally:
        again.close()


def test_custom_old_settings_are_preserved(tmp_path):
    data = tmp_path / "data"
    _write_old_settings(App(data), rate_limit_rps=2.0)

    app = App(data)
    try:
        assert app.settings.rate_limit_rps == 2.0  # user's choice kept
        assert app.settings.download_page_concurrency == 6  # new default
    finally:
        app.close()
