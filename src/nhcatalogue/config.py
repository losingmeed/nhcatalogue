"""Settings schema, defaults and validation.

Settings live in the SQLite ``settings`` KV table (JSON values) and are
layered over the defaults defined here. Everything the client/downloader/
server reads at runtime comes through :class:`Settings` so the UI can change
behaviour without a restart.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

DEFAULT_DOMAINS = ["nhentai.net"]
# Known public mirrors that expose the same JSON API shape. User-editable
# ordered list; the first reachable entry is used.
MIRROR_SUGGESTIONS = [
    "nhentai.net",
    "nhentai.to",
    "nhentai.xxx",
    "nhentai.net",  # kept once at top; de-dup happens at validation time
]

# Image / thumbnail CDN hosts (v2 /api/v2/cdn). Mirrors are tried in order on
# failure. The legacy i./t. hosts are kept as extra fallbacks.
IMAGE_HOSTS = ["i1.nhentai.net", "i2.nhentai.net", "i3.nhentai.net", "i4.nhentai.net"]
THUMB_HOSTS = ["t1.nhentai.net", "t2.nhentai.net", "t3.nhentai.net", "t4.nhentai.net"]
LEGACY_IMAGE_HOSTS = ["i.nhentai.net", "i5.nhentai.net", "i7.nhentai.net"]
LEGACY_THUMB_HOSTS = ["t.nhentai.net", "t7.nhentai.net"]

FALLBACK_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def default_data_dir() -> Path:
    """Per-user data dir: ~/.local/share/nhcatalogue on Linux, XDG override honoured."""
    xdg = Path.home() / ".local" / "share"
    import os

    base = Path(os.environ.get("XDG_DATA_HOME", xdg))
    return base / "nhcatalogue"


def default_library_root() -> Path:
    return default_data_dir() / "library"


@dataclass
class Settings:
    """All persisted configuration. Stored as JSON in the settings KV table."""

    # --- access / restrictions -------------------------------------------------
    domains: list[str] = field(default_factory=lambda: list(DEFAULT_DOMAINS))
    proxy: str | None = None  # http:// or socks5:// URL for a VPN, or None
    cf_clearance: str = ""  # pasted cf_clearance cookie value
    cf_user_agent: str = ""  # the exact UA the clearance was issued for
    flaresolverr_url: str = ""  # e.g. http://127.0.0.1:8191 (optional)
    rule34_domain: str = "rule34.xxx"  # rule 34 site host
    rule34_api_host: str = "api.rule34.xxx"  # rule 34 API host (separate)
    rule34_user_id: str = ""  # rule 34 account id (required by the API)
    rule34_api_key: str = ""  # rule 34 API key (required by the API)

    # --- politeness --------------------------------------------------------------
    # Fast preset by default: 4 req/s per CDN host, ~16 req/s aggregate across
    # i1–i4. Turbo (10 rps) and Polite (1 rps) are one click away in Settings.
    rate_limit_rps: float = 4.0  # per-host requests per second
    rate_limit_jitter: float = 0.15  # +- fraction of the interval
    host_concurrency: int = 6  # per-host concurrent request cap
    request_timeout: float = 30.0
    max_retries: int = 3

    # --- library / downloader ----------------------------------------------------
    # "" means "auto" → <data_dir>/library (resolved by App)
    library_root: str = ""
    folder_template: str = "{artist}/{id} - {title}"
    download_workers: int = 4
    download_page_concurrency: int = 6  # parallel page fetches inside one gallery
    dedup_mode: str = "skip"  # skip | symlink (second artist path)
    default_language: str = ""  # e.g. "english"; empty = no filter

    # --- cache -------------------------------------------------------------------
    # Lazy page cache for galleries that belong to at least one collection.
    # 0 disables it. Enforced as an LRU safety net.
    page_cache_max_mb: int = 2048
    # Temporary reader cache retention (seconds, idle time): pages for comics
    # that are previewed but not yet in a collection/download are kept briefly
    # and promoted to the permanent cache when the comic is saved.
    temp_page_cache_ttl_s: float = 10.0

    # --- watchlists ----------------------------------------------------------------
    watchlist_interval_hours: float = 6.0

    # --- ui / misc -----------------------------------------------------------------
    ui_prefs: dict[str, Any] = field(default_factory=dict)
    age_confirmed: bool = False
    first_run_done: bool = False
    reader_prefs: dict[str, Any] = field(
        default_factory=lambda: {"mode": "single", "rtl": False, "preload": 5}
    )

    # ---------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.domains:
            errors.append("domains: at least one domain is required")
        for d in self.domains:
            if not re.fullmatch(r"[A-Za-z0-9.\-]+(\.[A-Za-z]{2,})?", d or ""):
                errors.append(f"domains: invalid domain {d!r}")
        if self.proxy:
            if not re.match(r"^(https?|socks5)://", self.proxy):
                errors.append("proxy: must be an http://, https:// or socks5:// URL")
        if self.rate_limit_rps <= 0 or self.rate_limit_rps > 10:
            errors.append("rate_limit_rps: must be between 0 and 10 requests/sec")
        if not 1 <= self.host_concurrency <= 16:
            errors.append("host_concurrency: must be between 1 and 16")
        if not 1 <= self.download_workers <= 10:
            errors.append("download_workers: must be between 1 and 10")
        if not 1 <= self.download_page_concurrency <= 16:
            errors.append("download_page_concurrency: must be between 1 and 16")
        if self.page_cache_max_mb < 0:
            errors.append("page_cache_max_mb: cannot be negative")
        if self.temp_page_cache_ttl_s < 1:
            errors.append("temp_page_cache_ttl_s: minimum 1 second")
        if self.watchlist_interval_hours < 1:
            errors.append("watchlist_interval_hours: minimum 1 hour")
        if self.dedup_mode not in ("skip", "symlink"):
            errors.append("dedup_mode: must be 'skip' or 'symlink'")
        theme = self.ui_prefs.get("theme") if isinstance(self.ui_prefs, dict) else None
        if theme is not None:
            if not isinstance(theme, dict):
                errors.append("ui_prefs.theme: must be an object of colour tokens")
            else:
                for key, value in theme.items():
                    if not isinstance(key, str) or not re.fullmatch(
                        r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?", str(value or "")
                    ):
                        errors.append(f"ui_prefs.theme.{key}: must be a hex colour")
        # folder template vars
        allowed_vars = {"artist", "parody", "character", "language", "id", "title"}
        for var in re.findall(r"\{(\w+)\}", self.folder_template):
            if var not in allowed_vars:
                errors.append(f"folder_template: unknown variable {{{var}}}")
        if not self.folder_template or self.folder_template.strip("/\\ ") == "":
            errors.append("folder_template: cannot be empty")
        return errors

    def apply_patch(self, patch: dict[str, Any]) -> None:
        """Update from a dict, ignoring unknown keys, keeping types honest."""
        known = {f.name: f.type for f in fields(self)}
        for key, value in patch.items():
            if key not in known:
                continue
            cur = getattr(self, key)
            # coerce simple scalar types back from JSON strings
            if isinstance(cur, bool) and value is not None:
                value = bool(value)
            elif isinstance(cur, int) and not isinstance(cur, bool) and value is not None:
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    continue
            elif isinstance(cur, float) and value is not None:
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
            elif isinstance(cur, str):
                value = "" if value is None else str(value)
            elif isinstance(cur, dict) and isinstance(value, dict):
                merged = dict(cur)
                merged.update(value)
                value = merged
            elif isinstance(cur, list) and isinstance(value, list):
                value = [str(v).strip() for v in value if str(v).strip()]
            setattr(self, key, value)
        if not self.cf_clearance:
            self.cf_user_agent = self.cf_user_agent
        # de-dup domains while preserving order
        seen: set[str] = set()
        self.domains = [d for d in self.domains if not (d in seen or seen.add(d))]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        s = cls()
        s.apply_patch(data)
        return s


def library_subdir(app) -> Path:
    """Resolved library root for this app context (settings override or data dir)."""
    return app.library_root
