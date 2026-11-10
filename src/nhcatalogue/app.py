"""App context: one object wiring settings + db + client + downloader + sync.

Single source of truth for the server, CLI and desktop shell so they all run
the same code paths.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from .config import Settings, default_data_dir
from .db import Database


class App:
    def __init__(
        self,
        data_dir: str | Path | None = None,
        *,
        settings: Settings | None = None,
        db: Database | None = None,
    ):
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = db or Database(self.data_dir / "catalogue.sqlite3")
        self.settings = settings or Settings()
        self._load_settings()
        self.token = secrets.token_urlsafe(24)
        # lazily created shared components
        self._client = None
        self._downloader = None
        self._syncer = None
        self._page_cache = None

    def _load_settings(self) -> None:
        stored = self.db_sync_settings()
        raw: dict | None = None
        if "settings" in stored and isinstance(stored["settings"], dict):
            raw = stored["settings"]
        elif stored:
            raw = stored
        if raw is None:
            return
        self.settings = Settings.from_dict(raw)
        self._upgrade_old_defaults(raw)

    def _upgrade_old_defaults(self, raw: dict) -> None:
        """One-time move from the old polite defaults to the Fast preset.

        Settings written before ``download_page_concurrency`` existed carry the
        old 1 req/s defaults, which make downloads crawl. Upgrade only when the
        user never customised those knobs, then persist so it happens once.
        """
        if "download_page_concurrency" in raw:
            return
        try:
            untouched = (
                float(raw.get("rate_limit_rps", 1.0)) == 1.0
                and float(raw.get("rate_limit_jitter", 0.35)) == 0.35
                and int(raw.get("host_concurrency", 3)) == 3
                and int(raw.get("download_workers", 3)) == 3
            )
        except (TypeError, ValueError):
            untouched = False
        if not untouched:
            return
        self.settings.rate_limit_rps = 4.0
        self.settings.rate_limit_jitter = 0.15
        self.settings.host_concurrency = 6
        self.settings.download_workers = 4
        self.settings.download_page_concurrency = 6
        self.settings.page_cache_max_mb = 2048
        self.save_settings()

    def db_sync_settings(self) -> dict:
        """Synchronous settings read at boot (before the loop matters)."""
        import json

        try:
            rows = self.db.conn.execute("SELECT key, value FROM settings").fetchall()
        except Exception:
            return {}
        out = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value"])
            except (json.JSONDecodeError, TypeError):
                out[r["key"]] = r["value"]
        return out

    def save_settings(self) -> None:
        """Persist the current settings object as one JSON blob."""
        self.db.settings_set_sync("settings", self.settings.to_dict())
    # ------------------------------------------------------------- components

    @property
    def client(self):
        if self._client is None:
            from .client import NhentaiClient

            self._client = NhentaiClient(self.settings)
        return self._client

    @property
    def downloader(self):
        if self._downloader is None:
            from .downloader import Downloader

            self._downloader = Downloader(self)
        return self._downloader

    @property
    def syncer(self):
        if self._syncer is None:
            from .sync import SyncEngine

            self._syncer = SyncEngine(self)
        return self._syncer

    @property
    def library_root(self) -> Path:
        root = (
            Path(self.settings.library_root).expanduser()
            if self.settings.library_root
            else self.data_dir / "library"
        )
        root.mkdir(parents=True, exist_ok=True)
        return root

    @property
    def resolved_library_root(self) -> str:
        return str(self.library_root)

    @property
    def covers_dir(self) -> Path:
        d = self.data_dir / "covers"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def page_cache(self):
        """Lazy collection-only page cache (see :mod:`nhcatalogue.pagecache`)."""
        if self._page_cache is None:
            from .pagecache import PageCache

            self._page_cache = PageCache(
                self.data_dir / "page_cache",
                self.settings.page_cache_max_mb * 1024 * 1024,
            )
        return self._page_cache

    def close(self) -> None:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if self._downloader is not None:
                if loop.is_running():
                    loop.create_task(self._downloader.stop())
                else:
                    loop.run_until_complete(self._downloader.stop())
            if self._syncer is not None:
                self._syncer.stop()
            if self._client is not None:
                if loop.is_running():
                    loop.create_task(self._client.close())
                else:
                    loop.run_until_complete(self._client.close())
        except RuntimeError:
            pass
        self.db.close()
