"""The nhentai client — the ONLY component that touches the network.

Request strategy chain (plan §1), applied to every outbound request:

1. mirror/domain rotation (health check + demote, user-editable order)
2. browser TLS impersonation (curl_cffi ``impersonate="chrome"``; httpx fallback)
3. manual ``cf_clearance`` cookie + matching User-Agent injection
4. optional FlareSolverr solve for fresh clearance cookies
5. optional SOCKS/HTTP proxy for every host

On a detected Cloudflare challenge we escalate: next domain → … → FlareSolverr
→ BlockedError("blocked → what to do") which the UI renders as a banner.

API endpoints (nhentai public JSON API, v2):

* ``GET /api/v2/search?query=&page=&sort=``
* ``GET /api/v2/galleries/{id}``
* ``GET /api/v2/galleries?page=``
* ``GET /api/v2/galleries/tagged?tag_id=&page=&sort=``
* ``GET /api/v2/tags/ids?ids=1,2,3`` (resolve search-result tag ids)
* images: ``https://i{1-4}.nhentai.net/{page.path}``
  thumbs/covers: ``https://t{1-4}.nhentai.net/{cover.path}``
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Mapping
from typing import Any

from ..config import (
    FALLBACK_USER_AGENT,
    IMAGE_HOSTS,
    LEGACY_IMAGE_HOSTS,
    LEGACY_THUMB_HOSTS,
    THUMB_HOSTS,
    Settings,
)
from .challenge import looks_like_challenge
from .domains import DomainRotator
from .errors import BlockedError, NotFoundError, RateLimitedError
from .models import Gallery, SearchResult, Tag, normalize_type
from .ratelimit import RateLimiter
from .transport import FlareSolverrSession, Transport, TransportResponse, build_transport

log = logging.getLogger("nhcatalogue.client")

SORTS = ("date", "popular", "popular-month", "popular-week", "popular-today")

# nhentai v2 JSON API (v1 endpoints were retired: they now answer
# ``403 Use new API https://nhentai.net/api/v2/docs``).
SEARCH_PATH = "/api/v2/search"
ALL_GALLERIES_PATH = "/api/v2/galleries"
GALLERY_PATH = "/api/v2/galleries/{id}"
TAGGED_PATH = "/api/v2/galleries/tagged"
TAGS_BY_IDS_PATH = "/api/v2/tags/ids"
SEARCH_TAGS_PATH = "/api/v2/tags/search"
CONFIG_PATH = "/api/v2/config"
TAG_CHUNK = 50


class NhentaiClient:
    """Async client. Pass ``transport=`` for tests; otherwise the best real one."""

    def __init__(self, settings: Settings, transport: Transport | None = None):
        self.settings = settings
        self.transport = transport or build_transport(settings)
        self.rotator = DomainRotator(settings.domains)
        self.limiter = RateLimiter(
            settings.rate_limit_rps,
            settings.rate_limit_jitter,
            settings.host_concurrency,
        )
        # runtime-acquired clearance (from FlareSolverr) — kept separate from the
        # user-pasted one in settings so we don't overwrite it silently.
        self._runtime_cookies: dict[str, str] = {}
        self._runtime_ua: str | None = None
        self._flaresolverr: FlareSolverrSession | None = None
        self._tag_cache: dict[int, Tag] = {}
        self.last_blocked: BlockedError | None = None

    # ------------------------------------------------------------------ helpers

    def refresh_from_settings(self) -> None:
        """Re-read settings that may have changed at runtime (domains, limits, proxy)."""
        self.rotator.set_domains(self.settings.domains)
        self.limiter.update(
            self.settings.rate_limit_rps,
            self.settings.rate_limit_jitter,
            self.settings.host_concurrency,
        )

    def _base_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": FALLBACK_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"https://{self.settings.domains[0]}/" if self.settings.domains else "",
        }
        # strategy 3: manual cf_clearance binds to the UA it was issued for
        if self.settings.cf_clearance and self.settings.cf_user_agent:
            headers["User-Agent"] = self.settings.cf_user_agent
        elif self._runtime_ua:
            headers["User-Agent"] = self._runtime_ua
        return headers

    def _cookies(self) -> dict[str, str]:
        cookies: dict[str, str] = {}
        if self.settings.cf_clearance:
            cookies["cf_clearance"] = self.settings.cf_clearance
        cookies.update(self._runtime_cookies)
        return cookies

    def _image_headers(self) -> dict[str, str]:
        """Headers that match a browser loading an <img> from the CDN."""
        headers = self._base_headers()
        headers["Accept"] = (
            "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
        )
        headers["Sec-Fetch-Dest"] = "image"
        headers["Sec-Fetch-Mode"] = "no-cors"
        return headers

    @staticmethod
    def _host(url: str) -> str:
        try:
            return url.split("/", 3)[2]
        except IndexError:  # pragma: no cover - malformed url
            return url

    # ------------------------------------------------------- strategy chain

    async def _send(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        json_body: Mapping[str, Any] | None = None,
    ) -> TransportResponse:
        """Send a single request with rate limiting (no retries)."""
        host = self._host(url)
        await self.limiter.acquire(host)
        try:
            return await self.transport.request(
                method,
                url,
                headers=headers or self._base_headers(),
                cookies=self._cookies(),
                json_body=json_body,
            )
        finally:
            self.limiter.release(host)

    async def _flaresolverr_solve(self, url: str) -> bool:
        """Strategy 4: ask FlareSolverr for fresh clearance cookies."""
        if not self.settings.flaresolverr_url:
            return False
        if self._flaresolverr is None:
            self._flaresolverr = FlareSolverrSession(self.settings.flaresolverr_url)
        try:
            result = await self._flaresolverr.solve(url, self.transport)
        except Exception as e:
            log.warning("FlareSolverr failed: %s", e)
            self.last_blocked = BlockedError(f"FlareSolverr error: {e}")
            return False
        self._runtime_cookies = {
            k: v for k, v in result.get("cookies", {}).items() if k == "cf_clearance"
        }
        self._runtime_ua = result.get("user_agent") or None
        if self.settings.cf_clearance:
            # runtime clearance wins for future requests
            self.settings.cf_clearance = self._runtime_cookies.get("cf_clearance", "")
            self.settings.cf_user_agent = self._runtime_ua or self.settings.cf_user_agent
        return bool(self._runtime_cookies)

    async def _request_json(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        method: str = "GET",
        json_body: Mapping[str, Any] | None = None,
    ) -> Any:
        """Request a JSON API path through the full strategy chain.

        ``method``/``json_body`` allow POST endpoints (e.g. live tag search);
        the chain itself is identical for GET and POST.

        Chain: for each domain in rotation order → request → on challenge/block,
        escalate to the next domain; once all domains are exhausted, try
        FlareSolverr (if configured) and retry once with fresh cookies.
        Network errors retry with exponential backoff per domain.
        """
        qs = ""
        if params:
            from urllib.parse import urlencode

            qs = "?" + urlencode({k: v for k, v in params.items() if v is not None})

        strategies_tried: list[str] = []
        last_exc: Exception | None = None

        for attempt, round_ in enumerate(("first", "flaresolverr")):
            for domain in self.rotator.order():
                url = f"https://{domain}{path}{qs}"
                strategies_tried.append(f"{method} {url}")
                backoff = 0.5
                for _retry in range(max(1, self.settings.max_retries)):
                    try:
                        resp = await self._send(
                            url, method=method, json_body=json_body
                        )
                    except (TimeoutError, OSError) as e:
                        # network/timeout: backoff then retry same domain
                        last_exc = e
                        self.rotator.report_failure(domain, f"network: {e!r}")
                        strategies_tried.append(f"retry {domain} after {e.__class__.__name__}")
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 10.0)
                        continue

                    if resp.status == 404:
                        self.rotator.report_success(domain)
                        raise NotFoundError(path)
                    if resp.status == 429:
                        self.rotator.report_success(domain)
                        await asyncio.sleep(5.0 + random.uniform(0, 3))
                        continue

                    if looks_like_challenge(resp.status, resp.headers, resp.content):
                        last_exc = BlockedError(f"{domain}: challenge/block page")
                        self.rotator.report_failure(domain, "cf-challenge")
                        strategies_tried.append(f"{domain}: CF challenge → rotate domain")
                        break  # rotate to next domain immediately

                    if resp.status >= 500:
                        last_exc = BlockedError(f"{domain}: HTTP {resp.status}")
                        self.rotator.report_failure(domain, f"http {resp.status}")
                        strategies_tried.append(f"{domain}: HTTP {resp.status} → rotate")
                        break

                    # success
                    self.rotator.report_success(domain)
                    try:
                        return resp.json()
                    except ValueError as e:
                        # mirror quirk: non-JSON body — treat as domain failure
                        last_exc = e
                        self.rotator.report_failure(domain, "non-json body")
                        break

            if round_ == "first" and self.settings.flaresolverr_url and attempt == 0:
                target = f"https://{self.rotator.order()[0]}{path}{qs}"
                strategies_tried.append(f"FlareSolverr solve {target}")
                solved = await self._flaresolverr_solve(target)
                if solved:
                    strategies_tried.append("FlareSolverr: fresh cf_clearance acquired")
                    continue  # second round with the new cookies
                break

        msg = (
            "Blocked: every domain in the rotation list failed. "
            "Next steps: edit Settings → Domains (add/enable mirrors), paste a fresh "
            "cf_clearance + its User-Agent, set a FlareSolverr endpoint, or configure "
            "a SOCKS/HTTP proxy (VPN)."
        )
        err = BlockedError(msg, strategies_tried)
        self.last_blocked = err
        if isinstance(last_exc, RateLimitedError):  # pragma: no cover
            raise last_exc
        raise err

    # ------------------------------------------------------------ image URLs

    def page_url(self, gallery: Gallery, page: int, host: int | None = None) -> str:
        """Image URL builder with i1–i4 round-robin rotation.

        The rate limiter is per-host, so spreading pages across all four CDNs
        multiplies throughput without raising the per-host request rate. Pass
        ``host`` explicitly to pin a specific mirror (used by tests/fallbacks).
        """
        h = IMAGE_HOSTS[(page - 1 if host is None else host) % len(IMAGE_HOSTS)]
        p = gallery.pages[page - 1]
        if p.path:
            return f"https://{h}/{p.path.lstrip('/')}"
        return f"https://{h}/galleries/{gallery.media_id}/{page}.{p.ext}"

    def cover_url(self, gallery: Gallery, host: int | None = None) -> str:
        h = THUMB_HOSTS[
            ((gallery.id - 1) if host is None else host) % len(THUMB_HOSTS)
        ]
        if gallery.cover_path:
            return f"https://{h}/{gallery.cover_path.lstrip('/')}"
        if gallery.thumb_path:
            return f"https://{h}/{gallery.thumb_path.lstrip('/')}"
        return f"https://{h}/galleries/{gallery.media_id}/cover.{gallery.cover_ext}"

    def thumb_url(self, gallery: Gallery, host: int | None = None) -> str:
        h = THUMB_HOSTS[
            ((gallery.id - 1) if host is None else host) % len(THUMB_HOSTS)
        ]
        if gallery.thumb_path:
            return f"https://{h}/{gallery.thumb_path.lstrip('/')}"
        return f"https://{h}/galleries/{gallery.media_id}/thumb.{gallery.thumb_ext}"

    @staticmethod
    def _valid_image_response(resp: TransportResponse) -> bool:
        """Accept only non-empty bodies that aren't clearly HTML/JSON errors."""
        if not resp.content:
            return False
        ctype = str(resp.headers.get("content-type") or "").lower()
        if ctype and not (
            ctype.startswith("image/") or ctype.startswith("application/octet-stream")
        ):
            return False
        return True

    @staticmethod
    def _retry_after_seconds(resp: TransportResponse, fallback: float) -> float:
        raw = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
        try:
            return max(0.0, min(30.0, float(raw)))
        except (TypeError, ValueError):
            return fallback

    async def fetch_image(self, url: str) -> bytes:
        """Fetch raw image bytes through the same chain (used for covers/pages).

        Host rotation on CDN errors mirrors the API strategy: i1 → i2 → i3 →
        i4 (and t1 → t4 for thumbs), with the legacy i./t. hosts as fallbacks.
        429/5xx responses are retried with ``Retry-After``/exponential backoff
        before moving on to the next mirror.
        """
        parsed_host = url.split("/")[2]
        prefix = parsed_host.split(".")[0][:1]
        if prefix == "t":
            family = list(THUMB_HOSTS) + list(LEGACY_THUMB_HOSTS)
        else:
            family = list(IMAGE_HOSTS) + list(LEGACY_IMAGE_HOSTS)
        candidates = [parsed_host] + [h for h in family if h != parsed_host]
        last: Exception | None = None
        attempts = max(1, min(4, self.settings.max_retries))
        for host in candidates:
            alt_url = url.replace(parsed_host, host, 1)
            backoff = 0.5
            for attempt in range(attempts):
                try:
                    resp = await self._send(alt_url, headers=self._image_headers())
                except (TimeoutError, OSError) as e:
                    last = e
                    if attempt + 1 < attempts:
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 4.0)
                    continue
                if looks_like_challenge(resp.status, resp.headers, resp.content):
                    last = BlockedError(f"{host}: challenge on image CDN")
                    break  # rotate to the next host
                if resp.status == 429 or resp.status >= 500:
                    last = BlockedError(f"{host}: HTTP {resp.status} for image")
                    if attempt + 1 < attempts:
                        await asyncio.sleep(
                            self._retry_after_seconds(resp, backoff)
                        )
                        backoff = min(backoff * 2, 4.0)
                    continue
                if resp.status == 200 and self._valid_image_response(resp):
                    return resp.content
                last = BlockedError(f"{host}: HTTP {resp.status} for image")
                break  # non-retryable (404 etc.) → next mirror
        raise BlockedError(f"could not fetch image {url}: {last}", [url])

    # ------------------------------------------------------------- API methods

    async def _resolve_tags(self, galleries: list[Gallery]) -> None:
        """Best-effort tag resolution for v2 list items (one batched request).

        Search results only carry ``tag_ids``; the catalogue grid and cards use
        language/artist tags, so resolve them once per page. Failures are
        non-fatal — the search itself already succeeded.
        """
        wanted: set[int] = set()
        for g in galleries:
            if not g.tags:
                wanted.update(g.tag_ids)
        missing = sorted(i for i in wanted if i not in self._tag_cache)
        try:
            for start in range(0, len(missing), TAG_CHUNK):
                chunk = missing[start : start + TAG_CHUNK]
                if not chunk:
                    continue
                raw = await self._request_json(
                    TAGS_BY_IDS_PATH, {"ids": ",".join(str(i) for i in chunk)}
                )
                for item in raw if isinstance(raw, list) else []:
                    tag = Tag.from_api(item)
                    self._tag_cache[tag.id] = tag
        except Exception as e:  # tags are decorative here — never fail the search
            log.debug("tag resolution failed: %s", e)
        for g in galleries:
            if g.tags:
                continue
            g.tags = [self._tag_cache[i] for i in g.tag_ids if i in self._tag_cache]

    async def search(self, query: str, page: int = 1, sort: str = "") -> SearchResult:
        """Full nhentai query syntax: tag:, artist:, parody:, character:, group:,
        language:, category:, -tag exclusions, quoted phrases."""
        if not (query or "").strip():
            return await self.all_galleries(page)
        if sort and sort not in SORTS:
            sort = ""
        params: dict[str, Any] = {"query": query, "page": page}
        if sort:
            params["sort"] = sort
        raw = await self._request_json(SEARCH_PATH, params)
        result = SearchResult.from_api(raw, page)
        await self._resolve_tags(result.galleries)
        return result

    async def gallery(self, gallery_id: int) -> Gallery:
        raw = await self._request_json(GALLERY_PATH.format(id=int(gallery_id)))
        return Gallery.from_api(raw)

    async def all_galleries(self, page: int = 1) -> SearchResult:
        raw = await self._request_json(ALL_GALLERIES_PATH, {"page": page})
        result = SearchResult.from_api(raw, page)
        await self._resolve_tags(result.galleries)
        return result

    async def tagged(self, tag_id: int, page: int = 1, sort: str = "") -> SearchResult:
        if sort and sort not in SORTS:
            sort = ""
        params: dict[str, Any] = {"tag_id": int(tag_id), "page": page}
        if sort:
            params["sort"] = sort
        raw = await self._request_json(TAGGED_PATH, params)
        result = SearchResult.from_api(raw, page)
        await self._resolve_tags(result.galleries)
        return result

    async def search_tags(
        self, query: str = "", type_: str = "", limit: int = 10
    ) -> list[Tag]:
        """Live tag search/autocomplete (``POST /api/v2/tags/search``).

        An empty query returns the most popular tags, which makes the Browse
        picker useful for discovering tags before you have a local library.
        The site calls general tags type ``tag``; results are normalized to
        the app's ``general`` type.
        """
        body: dict[str, Any] = {
            "query": query or None,
            "limit": max(1, min(int(limit), 50)),
        }
        if type_:
            normalized = normalize_type(type_)
            body["type"] = "tag" if normalized == "general" else normalized
        raw = await self._request_json(
            SEARCH_TAGS_PATH, method="POST", json_body=body
        )
        tags = [Tag.from_api(item) for item in raw] if isinstance(raw, list) else []
        for tag in tags:
            self._tag_cache[tag.id] = tag
        return tags

    async def test_connection(self) -> dict:
        """Settings → 'Test connection' button. Returns a status dict."""
        try:
            raw = await self._request_json(CONFIG_PATH)
            servers = len(raw.get("image_servers") or []) if isinstance(raw, dict) else 0
            return {
                "ok": True,
                "message": (
                    f"Connected via {self.rotator.order()[0]} "
                    f"(v2 API, {servers} image CDNs)."
                ),
                "transport": self.transport.name,
                "domain": self.rotator.order()[0],
            }
        except BlockedError as e:
            return {
                "ok": False,
                "message": str(e),
                "transport": self.transport.name,
                "domain": self.rotator.order()[0] if self.rotator.domains else None,
                "strategies": e.strategies,
            }
        except Exception as e:  # pragma: no cover - defensive
            return {"ok": False, "message": f"{e.__class__.__name__}: {e}", "transport": "error"}

    async def close(self) -> None:
        await self.transport.close()

    async def __aenter__(self) -> NhentaiClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()
